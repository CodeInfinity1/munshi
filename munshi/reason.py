"""The reasoning layer: two implementations of one interface.

`AgentReasoner` runs a bounded tool-calling loop against Groq. It receives a
compact case brief -- deliberately not the whole context pack -- and decides for
itself what else to look at: the payer's other cases, the live downtime feed, what
has already been tried, the deterministic recovery score, or a policy dry-run on
an action it is unsure about. It ends by calling `submit_decision`.

`HeuristicReasoner` is a complete deterministic implementation of the same
interface, driven off the taxonomy. It is the zero-credential fallback, the
degradation target when anything about the model goes wrong, and an evaluation
arm in its own right -- which is how "is the model earning its cost, or is the
taxonomy doing all the work?" becomes a question with a number attached.

**Why an LLM is here at all.** Most of this pipeline is deterministic on purpose.
The model earns its place on three things, all judgement over heterogeneous
evidence rather than lookup: disambiguating opaque failures (Razorpay documents
`payment_failed` as "no specific error code received from gateway"), choosing
between defensible interventions and their timing, and writing the customer
message. It does not decide retryability, enforce limits, compute money, or
execute anything.

**What it returns is a proposal, not an instruction.** Every field is re-validated
against a closed vocabulary before it goes anywhere -- a schema-shaped response is
still an untrusted response -- and any failure at all degrades to the
deterministic path with the reason recorded on the case.
"""

from __future__ import annotations

import logging
from collections import Counter

from .config import settings
from .models import ACTION_TIERS, ROOT_CAUSES, Diagnosis, Plan
from .taxonomy import lookup

log = logging.getLogger("munshi.reason")

CHANNELS = ("email", "sms", "whatsapp", "none")
MAX_DELAY_HOURS = 336.0  # 14 days: the outer edge of any recovery window

class HeuristicReasoner:
    """Deterministic reasoner. The offline fallback, and an evaluation arm in its
    own right: it answers 'what does the taxonomy alone get you?'"""

    name = "heuristic"

    def decide(self, ctx: dict, toolbox=None) -> tuple[Diagnosis, Plan]:
        f = ctx["failure"]
        case, cust, dt = ctx["case"], ctx["customer"], ctx["downtime"]
        sem = lookup(f["error_reason"])
        kind = case["kind"]
        family = f["family"]

        evidence = [
            f"razorpay error_reason={f['error_reason']} source={f['error_source']}",
            f"taxonomy family={family} retryability={f['retryability']}",
        ]
        if dt.get("state") == "active":
            evidence.append(f"active {dt['severity']}-severity downtime on this instrument")

        # Root cause maps straight off the taxonomy family; the heuristic arm has
        # no way to notice when the context contradicts the failure code.
        cause = {
            "balance_dependent": "payer_balance", "customer_dropout": "customer_abandoned",
            "transient_infra": "gateway_transient", "limit_bound": "limit_exhausted",
            "instrument_dead": "instrument_dead", "mandate_broken": "mandate_invalid",
            "merchant_config": "merchant_misconfiguration", "integration_bug": "integration_defect",
            "risk_flagged": "risk_decline", "already_settled": "already_paid",
        }.get(family, "unknown")

        if kind == "invoice_overdue":
            cause = "payer_unwilling" if case["days_overdue"] > 45 else "payer_balance"
        elif kind == "checkout_abandoned":
            cause = "customer_abandoned"

        action, delay, msg = self._select(kind, family, sem, case, cust, dt,
                                          ctx["compliance"], ctx)
        channel = cust.get("preferred_channel") or "email" if msg else "none"
        recoverability = self._recoverability(family, kind, case, cust)

        diag = Diagnosis(
            root_cause=cause, confidence=0.6, recoverability=recoverability,
            rationale=f"{sem.family_label}. {sem.resolution_requires}.",
            evidence=evidence, reasoner=self.name,
        )
        plan = Plan(
            action_type=action, params={}, delay_hours=delay, channel=channel,
            message=msg, reasoner=self.name,
            justification=f"Taxonomy default for {family}: {sem.default_intervention}.",
        )
        return diag, plan

    def _select(self, kind, family, sem, case, cust, dt, comp, ctx):
        """Pick one action. Ordered so that hard stops come before interventions,
        and preconditions come before the action they gate."""
        amount = case["amount_inr"]

        if kind == "invoice_overdue":
            if case["days_overdue"] > 60 and amount >= 25_000:
                return "escalate_to_collections", 0.0, None
            return "send_reminder", 0.0, (
                f"Invoice {case['entity_id']} for Rs {amount:,.0f} is {case['days_overdue']} "
                "days overdue. Please use the payment link to settle it."
            )
        if kind == "checkout_abandoned":
            return "send_recovery_link", 1.0, (
                f"Your order of Rs {amount:,.0f} is still held. Complete the payment here."
            )

        # Bounded resources. Exhausting one avenue closes that avenue, not the case:
        # a customer who has had three messages may still have a retry left, and
        # closing the case there writes off collectable revenue.
        can_contact = (
            case["contacts_remaining"] > 0
            and not cust.get("contact_opt_out")
            and sem.contacts_customer
        )
        can_retry = case["retries_remaining"] > 0 and not sem.retry_is_futile

        # --- hard stops: nothing below should run for these ---------------------
        if family == "already_settled":
            return "suppress_case", 0.0, None
        if family == "risk_flagged":
            return "escalate_to_merchant_ops", 0.0, None
        if family == "integration_bug":
            return "open_engineering_ticket", 0.0, None

        # --- merchant-side faults: alert once, then probe ------------------------
        alerted = any(h["action"] == "escalate_to_merchant_ops" and h["outcome"] == "success"
                      for h in ctx["history"])
        if family == "merchant_config" and not alerted:
            # The customer cannot enable a disabled payment method. Contacting them
            # would be an actively wrong action.
            return "escalate_to_merchant_ops", 0.0, None

        # --- recurring debits: two preconditions before a retry is even legal ----
        if case["method"] == "emandate":
            if comp.get("mandate_amount_needs_customer_afa"):
                return "send_mandate_reauth_link", 0.0, (
                    f"The auto-pay debit of Rs {amount:,.0f} needs your re-authorisation, "
                    "as it is above the limit for automatic recurring payments. "
                    "Please approve it using the link."
                )
            if not comp.get("pre_debit_notification_sent"):
                return "send_reminder", 0.0, (
                    f"Advance notice: we will attempt the auto-pay debit of "
                    f"Rs {amount:,.0f} after 24 hours. Please keep sufficient balance, "
                    "or use the link to pay now."
                )

        age = case["age_hours"]

        def backoff(hours: float) -> float:
            """Remaining wait, measured from the failure rather than from now."""
            return max(0.0, hours - age)

        if family == "merchant_config" and can_retry:
            return "retry_payment", backoff(24.0), None

        # Instrument- and mandate-level failures are only ever fixed by the customer
        # supplying something new, so a retry is not a fallback here -- it is a
        # guaranteed-zero action, and `can_retry` is already False for both.
        if family == "instrument_dead" and can_contact:
            return "send_instrument_update_link", 0.0, (
                f"Your payment of Rs {amount:,.0f} could not be completed: "
                f"{sem.description} Please add a different payment method."
            )
        if family == "mandate_broken" and can_contact:
            return "send_mandate_reauth_link", 0.0, (
                f"The auto-pay mandate for Rs {amount:,.0f} needs to be set up again. "
                "Please re-authorise it using the link."
            )
        if family == "customer_dropout" and can_contact:
            return "send_recovery_link", 0.0, (
                f"Your payment of Rs {amount:,.0f} did not go through. "
                "You can complete it using this link."
            )
        if family == "transient_infra":
            active = dt.get("state") == "active"
            if active and can_retry:
                from .downtime import MAX_CONSECUTIVE_HOLDS

                if dt.get("consecutive_holds", 0) >= MAX_CONSECUTIVE_HOLDS and can_contact:
                    # The outage has outlasted our patience. Stop waiting on the
                    # broken rail and let the customer pay on a working one.
                    return "send_recovery_link", 0.0, (
                        f"Your payment of Rs {amount:,.0f} could not be completed because "
                        "your bank is currently facing an outage. You can pay using a "
                        "different method here."
                    )
                # A live outage is measured from now: it is happening now.
                return "retry_payment", float(dt.get("hold_hours", 2.0)), None
            if can_retry:
                return "retry_payment", backoff(float(sem.min_backoff_hours or 2)), None
        # balance_dependent and limit_bound: the retry has to wait on a precondition
        # the payer controls. Tell them first -- the message converts on warm intent
        # while the retry waits, and the two draw on different budgets.
        wait = backoff(float(sem.min_backoff_hours or 24))
        if can_contact and case["contacts_sent"] == 0 and wait >= 6:
            return "send_recovery_link", 0.0, (
                f"Your payment of Rs {amount:,.0f} could not be completed: "
                f"{sem.description} You can pay using this link whenever you are ready."
            )
        if can_retry:
            return "retry_payment", wait, None
        if can_contact:
            # Retries are spent, but the payer can still be handed a link to pay
            # directly. This is the recovery a contact-budget-ends-the-case design
            # throws away.
            return "send_recovery_link", 0.0, (
                f"Your payment of Rs {amount:,.0f} is still pending. "
                "You can complete it using this link."
            )
        return "no_action", 0.0, None

    def _recoverability(self, family, kind, case, cust) -> float:
        base = {
            "already_settled": 0.0, "risk_flagged": 0.05, "mandate_broken": 0.35,
            "instrument_dead": 0.4, "integration_bug": 0.45, "merchant_config": 0.5,
            "limit_bound": 0.55, "customer_dropout": 0.6, "balance_dependent": 0.62,
            "transient_infra": 0.7,
        }.get(family, 0.4)
        if kind == "invoice_overdue":
            base = 0.7 - min(0.45, case["days_overdue"] * 0.006)
        # Loyalty does not make already-collected or risk-declined money collectable.
        if cust.get("is_established") and base > 0.1:
            base += 0.08
        if case["attempts_before_munshi"] >= 2:
            base -= 0.1
        return round(max(0.0, min(1.0, base)), 3)


class AgentReasoner:
    """Tool-using agent. Proposes; never executes."""

    name = "agent"

    def __init__(self, provider=None, fallback: HeuristicReasoner | None = None,
                 max_turns: int | None = None):
        from .llm import build_provider

        self.provider = provider or build_provider()
        self.model = self.provider.model
        self._fallback = fallback or HeuristicReasoner()
        self._max_turns = max_turns or settings().agent_max_turns
        self.degraded = 0
        self.degrade_reasons: Counter = Counter()
        self.last_trace: dict | None = None

    def decide(self, ctx: dict, toolbox=None) -> tuple[Diagnosis, Plan]:
        from .agent.loop import AgentFailed, run_agent
        from .llm.base import LLMError

        if toolbox is None:
            # Without tools there is no agent, only a prompt. Refuse to pretend.
            return self._degrade(ctx, "no toolbox supplied")
        try:
            raw, trace = run_agent(self.provider, toolbox, build_brief(ctx),
                                   max_turns=self._max_turns)
            diag, plan = self._validate(raw, trace)
            self.last_trace = trace.summary()
            return diag, plan
        except (AgentFailed, LLMError, ValueError) as exc:
            return self._degrade(ctx, f"{type(exc).__name__}: {exc}")

    def _degrade(self, ctx: dict, reason: str) -> tuple[Diagnosis, Plan]:
        """Any model failure lands here. Financial state is never touched by it."""
        self.degraded += 1
        self.degrade_reasons[reason.split(":")[0]] += 1
        log.warning("agent degraded for %s: %s", ctx["case"]["id"], reason)
        diag, plan = self._fallback.decide(ctx)
        diag.rationale = f"[agent unavailable: {reason[:120]}] {diag.rationale}"
        diag.reasoner = "heuristic"
        self.last_trace = {"provider": self.provider.name, "model": self.model,
                           "outcome": "degraded", "degrade_reason": reason[:200],
                           "turns": 0, "tools_used": [], "tool_calls": []}
        return diag, plan

    def _validate(self, raw: dict, trace) -> tuple[Diagnosis, Plan]:
        """Trust nothing. A schema-shaped response is still an untrusted response."""
        if not isinstance(raw, dict):
            raise ValueError("submit_decision arguments were not an object")
        action = raw.get("action_type")
        if action not in ACTION_TIERS:
            raise ValueError(f"action_type {action!r} is outside the closed vocabulary")
        cause = raw.get("root_cause")
        if cause not in ROOT_CAUSES:
            raise ValueError(f"root_cause {cause!r} is outside the closed vocabulary")
        channel = raw.get("channel") if raw.get("channel") in CHANNELS else "none"

        diag = Diagnosis(
            root_cause=cause,
            confidence=_clamp(raw.get("confidence", 0.5), 0.0, 1.0),
            recoverability=_clamp(raw.get("recoverability", 0.5), 0.0, 1.0),
            rationale=str(raw.get("diagnosis_rationale", ""))[:320],
            evidence=[str(e)[:200] for e in (raw.get("evidence") or [])][:4],
            reasoner=self.name, model=self.model,
        )
        message = (raw.get("message") or "").strip() or None
        plan = Plan(
            action_type=action, params={"agent_trace": trace.summary()},
            delay_hours=_clamp(raw.get("delay_hours", 0), 0.0, MAX_DELAY_HOURS),
            channel=channel, message=message,
            justification=str(raw.get("justification", ""))[:320],
            reasoner=self.name,
        )
        return diag, plan


def build_brief(ctx: dict) -> dict:
    """The opening brief.

    Deliberately smaller than the full context pack. Handing the agent everything
    up front would make its tools decorative; this gives it the case and the
    facts it always needs, and leaves the payer's history, the downtime feed, the
    recovery score and the policy dry-run to be fetched when they matter.
    """
    f, c = ctx["failure"], ctx["case"]
    return {
        "case_id": c["id"],
        "case": {
            "id": c["id"], "kind": c["kind"], "amount_inr": c["amount_inr"],
            "method": c["method"], "age_hours": c["age_hours"],
            "days_overdue": c["days_overdue"],
            "attempts_by_munshi": c["munshi_attempts"],
            "attempts_before_munshi": c["attempts_before_munshi"],
            "contacts_sent": c["contacts_sent"],
            "retries_remaining": c["retries_remaining"],
            "contacts_remaining": c["contacts_remaining"],
            "materiality": ctx["money"]["materiality"],
            "is_recurring": ctx["money"]["is_recurring"],
        },
        "failure": {
            "error_source": f["error_source"], "error_step": f["error_step"],
            "error_reason": f["error_reason"], "family": f["family"],
            "retry_on_same_instrument_is_futile": f["retry_on_same_instrument_is_futile"],
            "who_must_act": f["who_must_act"],
            "razorpay_description": f["razorpay_description"],
            "resolution_requires": f["resolution_requires"],
            "min_backoff_hours": f["min_backoff_hours"],
        },
        "compliance": ctx["compliance"],
        "tool_hint": "Read tools are free. Reach for get_downtime_status when the failure "
                     "could be infrastructural, get_payment_history when the code is "
                     "ambiguous, get_recovery_history when attempts have already been "
                     "spent, and check_policy before proposing anything you are unsure of.",
    }


def _clamp(v, lo, hi) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return lo


def build_reasoner(force: str | None = None):
    """`force` pins an arm: 'heuristic', 'agent' (Groq), or 'mock-agent'.

    Unforced, this follows MUNSHI_REASONER. The default never presents a stand-in
    as a model: with no credential it runs the deterministic reasoner and says so.
    """
    mode = force or settings().effective_reasoner
    if mode in ("heuristic",):
        return HeuristicReasoner()
    if mode in ("agent-mock", "mock-agent"):
        from .llm.mock_provider import MockProvider

        return AgentReasoner(provider=MockProvider())
    return AgentReasoner()
