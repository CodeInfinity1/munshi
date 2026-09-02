"""The reasoning layer: diagnose a failure, then propose one intervention.

**Why an LLM is here at all.** Most of this pipeline is deterministic on purpose.
The model earns its place on exactly three things, all of which are judgement over
heterogeneous evidence rather than lookup:

1. *Disambiguating opaque failures.* Razorpay documents `payment_failed` as
   "no specific error code received from gateway". Whether that particular
   Rs 84,000 decline is an outage, a balance problem or a dying card is a
   weighing of downtime state, customer history, amount and timing. A lookup
   table cannot do it; a rule tree for it would be a worse model.
2. *Choosing between defensible interventions and their timing.* "Retry at 20:00
   because this payer has settled at 20:00 eleven times" versus "send an
   instrument-update link now" is a trade-off across signals that do not reduce
   to one ordering.
3. *Writing the actual customer message*, in register, referencing the real
   reason, without a template's tell.

**What it is not allowed to touch.** Retryability comes from the taxonomy. Limits,
windows and stopping rules come from the policy engine. Money is arithmetic.
Every field the model returns is validated against a closed vocabulary, and a
malformed or out-of-vocabulary response falls back to the deterministic reasoner
rather than being coerced into something plausible.

`HeuristicReasoner` is a full deterministic implementation of the same interface.
It is the offline fallback, and it is also an evaluation arm: running the batch
through both is how we find out whether the model is actually adding recovery
rather than just adding cost.
"""

from __future__ import annotations

import json
import logging

from .config import settings
from .models import ACTION_TIERS, ROOT_CAUSES, Diagnosis, Plan
from .taxonomy import lookup

log = logging.getLogger("munshi.reason")

CHANNELS = ("email", "sms", "whatsapp", "none")
MAX_DELAY_HOURS = 336.0  # 14 days: the outer edge of any recovery window

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string", "enum": list(ROOT_CAUSES)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "recoverability": {"type": "number", "minimum": 0, "maximum": 1},
        "diagnosis_rationale": {"type": "string", "maxLength": 320},
        "evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "action_type": {"type": "string", "enum": sorted(ACTION_TIERS)},
        "delay_hours": {"type": "number", "minimum": 0, "maximum": MAX_DELAY_HOURS},
        "channel": {"type": "string", "enum": list(CHANNELS)},
        "message": {"type": "string", "maxLength": 480},
        "justification": {"type": "string", "maxLength": 320},
    },
    "required": ["root_cause", "confidence", "recoverability", "diagnosis_rationale",
                 "evidence", "action_type", "delay_hours", "channel", "message",
                 "justification"],
    "additionalProperties": False,
}

SYSTEM = """You are the reasoning core of Munshi, a revenue-recovery agent for Indian \
merchants on Razorpay. You are given one revenue-risk case with a fully resolved \
context pack, and you return one diagnosis and one proposed intervention.

WHAT HAS ALREADY BEEN DECIDED WITHOUT YOU
- `failure.retry_on_same_instrument_is_futile` is derived from Razorpay's published \
failure taxonomy. When it is true, a retry on this instrument cannot succeed. Do not \
propose one; propose the action that changes the precondition instead.
- `failure.who_must_act` says whose problem this is. When it is `merchant` or \
`engineering`, contacting the customer is an actively wrong action -- the customer \
cannot fix a disabled payment method or a malformed order request.
- `downtime` is Razorpay's live Payment Downtime feed for this exact instrument. An \
active high or medium severity downtime means a retry now is near-worthless; the \
useful move is to wait for it to clear.
- `compliance` reports whether the RBI Fair Practices Code contact window \
(08:00-19:00 local) and the NPCI non-peak auto-debit windows are currently open.
- Retry caps, contact caps, cooldowns, exposure limits and stopping rules are \
enforced downstream by a policy engine you cannot override. Propose the action you \
believe is right; it will be checked.

YOUR JUDGEMENT IS WANTED ON
- Root cause, when the failure code is ambiguous or the context contradicts it.
- Which intervention is worth spending a bounded attempt on, and WHEN.
- Whether this money is worth chasing at all. Proposing `no_action` on a case with \
low recoverability is a correct and valuable answer, not a failure to engage.
- The customer-facing message, when one is warranted.

TIMING. `delay_hours` is measured from NOW, but the preconditions are measured from when the failure happened -- `case.age_hours`. A 24-hour balance backoff on a failure that is already six days old has been satisfied for five days; waiting another 24 hours burns window for nothing. Subtract the age before you wait. Use timing deliberately:
- Balance problems: time the retry to when money plausibly lands, and prefer the \
customer's `typical_success_hour_local` when one is known.
- Active downtime: wait past `downtime.hold_hours`.
- Customer who just abandoned an authentication: intent decays in hours, act fast.
- Outside the contact window: the policy engine will defer contact to 08:00 local. \
You do not need to pad for it, but do not fight it.

MESSAGE. Plain, specific, Indian-English business register. Reference the real \
reason and the real amount. No emoji, no guilt, no fake urgency, no invented \
offers, discounts, deadlines or account details. Empty string when the action \
does not contact anyone.

Return only the structured decision object. Keep `diagnosis_rationale` and \
`justification` to one or two sentences of stated reasoning -- they are written into \
a merchant-visible audit trail."""


class HeuristicReasoner:
    """Deterministic reasoner. The offline fallback, and an evaluation arm in its
    own right: it answers 'what does the taxonomy alone get you?'"""

    name = "heuristic"

    def decide(self, ctx: dict) -> tuple[Diagnosis, Plan]:
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
                return "schedule_retry", float(dt.get("hold_hours", 2.0)), None
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
            return "schedule_retry", wait, None
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


class LLMReasoner:
    """Anthropic-backed reasoner with a hard schema and a deterministic fallback."""

    name = "llm"

    def __init__(self, fallback: HeuristicReasoner | None = None):
        import anthropic

        s = settings()
        self._client = anthropic.Anthropic(api_key=s.anthropic_api_key)
        self._model = s.llm_model
        self._effort = s.llm_effort
        self._fallback = fallback or HeuristicReasoner()
        self.degraded = 0  # count of cases that fell back, surfaced in the run summary

    def decide(self, ctx: dict) -> tuple[Diagnosis, Plan]:
        try:
            raw = self._call(ctx)
            return self._validate(raw, ctx)
        except Exception as exc:  # noqa: BLE001 - any model failure degrades, never crashes
            self.degraded += 1
            log.warning("reasoner degraded to heuristic for %s: %s", ctx["case"]["id"], exc)
            diag, plan = self._fallback.decide(ctx)
            diag.rationale = f"[LLM unavailable: {type(exc).__name__}] {diag.rationale}"
            return diag, plan

    def _call(self, ctx: dict) -> dict:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=4000,
            # The system prompt is byte-stable across every case in the batch, so it
            # caches; only the per-case context pack is uncached input.
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            output_config={
                "effort": self._effort,
                "format": {"type": "json_schema", "schema": DECISION_SCHEMA},
            },
            messages=[{"role": "user", "content": json.dumps(ctx, separators=(",", ":"))}],
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"model refused: {getattr(resp.stop_details, 'category', None)}")
        text = next(b.text for b in resp.content if b.type == "text")
        return json.loads(text)

    def _validate(self, raw: dict, ctx: dict) -> tuple[Diagnosis, Plan]:
        """Trust nothing. A schema-shaped response is still an untrusted response."""
        action = raw.get("action_type")
        if action not in ACTION_TIERS:
            raise ValueError(f"action_type {action!r} is outside the closed vocabulary")
        cause = raw.get("root_cause")
        if cause not in ROOT_CAUSES:
            raise ValueError(f"root_cause {cause!r} is outside the closed vocabulary")
        channel = raw.get("channel") if raw.get("channel") in CHANNELS else "none"

        clamp = lambda v, lo, hi: max(lo, min(hi, float(v)))  # noqa: E731
        diag = Diagnosis(
            root_cause=cause,
            confidence=clamp(raw.get("confidence", 0.5), 0.0, 1.0),
            recoverability=clamp(raw.get("recoverability", 0.5), 0.0, 1.0),
            rationale=str(raw.get("diagnosis_rationale", ""))[:320],
            evidence=[str(e)[:200] for e in (raw.get("evidence") or [])][:4],
            reasoner=self.name, model=self._model,
        )
        message = (raw.get("message") or "").strip() or None
        plan = Plan(
            action_type=action, params={},
            delay_hours=clamp(raw.get("delay_hours", 0), 0.0, MAX_DELAY_HOURS),
            channel=channel, message=message,
            justification=str(raw.get("justification", ""))[:320],
            reasoner=self.name,
        )
        return diag, plan


def build_reasoner(force: str | None = None):
    """`force` is used by the evaluation harness to pin an arm."""
    if force == "heuristic" or (force is None and not settings().llm_available):
        return HeuristicReasoner()
    return LLMReasoner()
