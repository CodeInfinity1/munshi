"""The tools the agent may call.

Two rules govern this surface, and they are what make it safe to hand a model:

**Nothing here moves money.** There is no `retry_payment` tool, no
`create_payment_link` tool, no `send_message` tool. Every tool is a read, a
calculation, or a dry-run. The only way the agent affects the world is by calling
`submit_decision`, which *proposes* an action that the deterministic policy engine
and the executor then handle. A fully compromised model cannot execute a payment.

**`check_policy` is a dry run, not a bypass.** The agent can ask what the policy
engine would say about a candidate action, and the same engine re-evaluates
whatever it finally submits. Consulting policy costs nothing and grants nothing.

The read tools exist because a fixed context pack is not agency. A case whose
failure code is ambiguous deserves a look at the payer's history; one sitting in
an outage deserves a look at the downtime feed; one that has already been worked
deserves a look at what was tried. Which of those matter is case-dependent, which
is exactly the kind of choice worth delegating.
"""

from __future__ import annotations

import sqlite3
import threading

from .. import db, downtime
from ..db import jload
from ..models import ACTION_TIERS, ROOT_CAUSES, Plan
from ..policy import POLICY
from ..taxonomy import all_reasons, lookup
from ..triage import recovery_score

CHANNELS = ("email", "sms", "whatsapp", "none")
MAX_DELAY_HOURS = 336.0


class ToolError(ValueError):
    """A tool was called with arguments it cannot act on. Returned to the model
    as a result, not raised -- a bad call is a turn to learn from, not a crash."""


class Toolbox:
    """Tools bound to one case. Every call is recorded for the audit trail."""

    def __init__(self, conn: sqlite3.Connection, case: dict, ctx: dict, now: int, policy,
                 lock: threading.Lock | None = None):
        self.conn = conn
        self.case = case
        self.ctx = ctx
        self.now = now
        self.policy = policy
        # Reasoning fans out across threads, and every read tool touches the same
        # SQLite connection. `check_same_thread=False` permits use from another
        # thread; it does not permit *concurrent* use, which surfaces as
        # "bad parameter or other API misuse" under load. Reads are microseconds
        # and the model call is the whole latency, so serialising them here costs
        # nothing measurable and removes the race entirely.
        self._lock = lock or threading.Lock()
        self.calls: list[dict] = []

    # -- dispatch ---------------------------------------------------------
    def call(self, name: str, args: dict) -> dict:
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return {"error": f"unknown tool {name!r}", "available": sorted(TOOL_NAMES)}
        try:
            with self._lock:
                out = fn(**args)
        except TypeError as exc:
            out = {"error": f"bad arguments for {name}: {exc}"}
        except ToolError as exc:
            out = {"error": str(exc)}
        self.calls.append({"tool": name, "arguments": args, "result": out})
        return out

    # -- reads ------------------------------------------------------------
    def t_get_customer_context(self, case_id: str = "") -> dict:
        """Payer profile and settlement track record."""
        return self.ctx["customer"]

    def t_get_payment_history(self, case_id: str = "", limit: int = 10) -> dict:
        """Other revenue-risk cases for the same payer, and how they ended."""
        rows = db.rows(
            self.conn,
            "SELECT id, kind, amount_paise, error_reason, state, stop_reason, recovered_paise"
            " FROM cases WHERE customer_id = ? AND id != ? ORDER BY opened_at DESC LIMIT ?",
            (self.case["customer_id"], self.case["id"], max(1, min(int(limit), 25))),
        )
        return {
            "payer": self.case["customer_id"],
            "other_cases": [dict(r) for r in rows],
            "note": "History for the same payer. An instrument that failed on one case is "
                    "likely to fail on another.",
        }

    def t_get_failure_semantics(self, error_reason: str) -> dict:
        """Razorpay's documented meaning for any reason code, and what it implies."""
        sem = lookup(error_reason)
        if sem.reason == "unknown" and error_reason not in ("unknown", "", None):
            return {"error": f"{error_reason!r} is not in the taxonomy",
                    "known_reasons_sample": all_reasons()[:20],
                    "fallback": _semantics(sem)}
        return _semantics(sem)

    def t_get_downtime_status(self, case_id: str = "") -> dict:
        """Razorpay's Payment Downtime feed for this case's exact instrument."""
        status = downtime.status_for(self.conn, self.case, self.now)
        return {**status, "consecutive_holds": self.case.get("downtime_holds", 0)}

    def t_get_recovery_history(self, case_id: str = "") -> dict:
        """What Munshi has already attempted on this case, and what happened."""
        rows = db.rows(
            self.conn,
            "SELECT action_type, policy_decision, outcome, recovered_paise, proposed_at,"
            " executed_at, policy_rules FROM actions WHERE case_id = ? ORDER BY proposed_at",
            (self.case["id"],),
        )
        return {
            "attempts_used": self.case["attempts"],
            "attempts_remaining": max(0, POLICY["max_recovery_attempts"] - self.case["attempts"]),
            "contacts_used": self.case["contacts_sent"],
            "contacts_remaining": max(
                0, POLICY["max_customer_contacts"] - self.case["contacts_sent"]),
            "history": [
                {"action": r["action_type"], "policy": r["policy_decision"],
                 "outcome": r["outcome"], "recovered_paise": r["recovered_paise"],
                 "hours_ago": round((self.now - r["proposed_at"]) / 3600, 1),
                 "blocked_by": [x["rule"] for x in jload(r["policy_rules"], []) if not x["passed"]]}
                for r in rows
            ],
        }

    def t_calculate_recovery_score(self, case_id: str = "") -> dict:
        """Deterministic expected recoverable value, fully decomposed."""
        return recovery_score(self.case, self.ctx)

    # -- advisory ---------------------------------------------------------
    def t_check_policy(self, action_type: str, delay_hours: float = 0.0,
                       case_id: str = "") -> dict:
        """Ask what the policy engine would say. A dry run: it consumes nothing,
        and the same engine re-checks whatever is finally submitted."""
        if action_type not in ACTION_TIERS:
            raise ToolError(
                f"{action_type!r} is not an action. Valid actions: {sorted(ACTION_TIERS)}")
        plan = Plan(action_type=action_type, params={},
                    delay_hours=_clamp(delay_hours, 0, MAX_DELAY_HOURS),
                    channel=None, message=None, justification="policy dry run")
        d = self.policy.evaluate(self.case, plan, self.ctx, self.now, dry_run=True)
        return {
            "action_type": action_type,
            "tier": ACTION_TIERS[action_type],
            "decision": d.decision,
            "stop_reason": d.stop_reason,
            "would_reschedule_to_hours": (
                round((d.reschedule_at - self.now) / 3600, 1) if d.reschedule_at else None),
            "failed_rules": [
                {"rule": r.rule, "detail": r.detail} for r in d.rules if not r.passed],
            "rules_evaluated": len(d.rules),
        }

    # -- terminal ---------------------------------------------------------
    def t_submit_decision(self, **kw) -> dict:
        """Handled by the loop, which validates and ends the run."""
        return {"error": "submit_decision is terminal and is handled by the agent loop"}


def _semantics(sem) -> dict:
    return {
        "reason": sem.reason,
        "family": sem.family,
        "family_label": sem.family_label,
        "retryability": sem.retryability,
        "retry_on_same_instrument_is_futile": sem.retry_is_futile,
        "who_must_act": sem.blame,
        "customer_can_resolve": sem.contacts_customer,
        "razorpay_description": sem.description,
        "razorpay_next_step": sem.next_step,
        "resolution_requires": sem.resolution_requires,
        "min_backoff_hours": sem.min_backoff_hours,
    }


def _clamp(v, lo, hi) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return lo


_CASE_ID = {"case_id": {"type": "string", "description": "The case under decision."}}

#: The decision the agent must ultimately submit. Enums are generated from the
#: real vocabularies, so the schema and the policy engine can never disagree.
DECISION_PARAMS = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string", "enum": list(ROOT_CAUSES),
                       "description": "Underlying cause, not the error code."},
        "confidence": {"type": "number", "description": "0-1 confidence in the root cause."},
        "recoverability": {"type": "number",
                           "description": "0-1 chance this money is recoverable at all."},
        "diagnosis_rationale": {"type": "string",
                                "description": "One or two sentences. Merchant-visible."},
        "evidence": {"type": "array", "items": {"type": "string"},
                     "description": "Up to 4 short facts you relied on."},
        "action_type": {"type": "string", "enum": sorted(ACTION_TIERS),
                        "description": "The single intervention to propose."},
        "delay_hours": {"type": "number",
                        "description": "Hours from now to act. 0 means immediately."},
        "channel": {"type": "string", "enum": list(CHANNELS)},
        "message": {"type": "string",
                    "description": "Customer-facing text, or empty when not contacting."},
        "justification": {"type": "string",
                          "description": "Why this action and this timing. Merchant-visible."},
    },
    "required": ["root_cause", "confidence", "recoverability", "diagnosis_rationale",
                 "evidence", "action_type", "delay_hours", "channel", "message",
                 "justification"],
    "additionalProperties": False,
}

from .._toolspec import spec  # noqa: E402  (tiny local helper, imported late by design)

TOOL_SPECS = [
    spec("get_customer_context",
         "Payer segment, tenure, lifetime value, prior successes and failures, contact "
         "opt-out, and the local hour at which this payer has historically settled.",
         _CASE_ID),
    spec("get_payment_history",
         "Other revenue-risk cases for the same payer and how they ended. Use when the "
         "failure code is ambiguous and the payer's pattern would disambiguate it.",
         {**_CASE_ID, "limit": {"type": "integer", "description": "Max cases, default 10."}}),
    spec("get_failure_semantics",
         "Razorpay's documented meaning for a failure reason code: which family it belongs "
         "to, whether a retry on the same instrument can ever succeed, who must act, and "
         "what would have to change for recovery to be possible.",
         {"error_reason": {"type": "string", "description": "A Razorpay error_reason code."}},
         required=["error_reason"]),
    spec("get_downtime_status",
         "Razorpay's Payment Downtime feed for this case's exact instrument (issuer, bank, "
         "UPI handle or PSP): whether an outage is active, scheduled or recently resolved, "
         "its severity, and how many times we have already held this case waiting.",
         _CASE_ID),
    spec("get_recovery_history",
         "What Munshi has already attempted on this case, what the policy engine said each "
         "time, what happened, and how much retry and contact budget is left.",
         _CASE_ID),
    spec("calculate_recovery_score",
         "Deterministic expected recoverable value for this case, decomposed into the "
         "factors that produced it.",
         _CASE_ID),
    spec("check_policy",
         "Dry-run a candidate action against the policy engine and see the verdict and any "
         "failing rules, without consuming anything. The same engine re-checks whatever you "
         "finally submit, so this cannot be used to get around a rule -- only to avoid "
         "proposing something that would be refused. IMPORTANT: a 'deny' that carries "
         "would_reschedule_to_hours is temporary -- the case is simply not due yet, and "
         "proposing the action anyway is correct because the engine will schedule it. Only "
         "a 'deny' with a stop_reason and no reschedule is permanent.",
         {**_CASE_ID,
          "action_type": {"type": "string", "enum": sorted(ACTION_TIERS)},
          "delay_hours": {"type": "number", "description": "Hours from now, default 0."}},
         required=["action_type"]),
    spec("submit_decision",
         "Submit your final diagnosis and the single intervention to propose. This ends "
         "your turn. The action is NOT executed by you: it goes to the policy engine and "
         "then to the executor.",
         DECISION_PARAMS["properties"], required=DECISION_PARAMS["required"]),
]

TOOL_NAMES = {t.name for t in TOOL_SPECS}
READ_ONLY_TOOLS = TOOL_NAMES - {"submit_decision"}
