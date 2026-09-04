"""Domain vocabulary. Small, closed sets -- the LLM is only ever allowed to pick
from these, never to invent new ones."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Paise = int  # money is integer paise, everywhere, always


class CaseKind:
    PAYMENT_FAILURE = "payment_failure"
    SUBSCRIPTION_FAILURE = "subscription_failure"
    INVOICE_OVERDUE = "invoice_overdue"
    CHECKOUT_ABANDONED = "checkout_abandoned"
    ALL = (PAYMENT_FAILURE, SUBSCRIPTION_FAILURE, INVOICE_OVERDUE, CHECKOUT_ABANDONED)


class CaseState:
    OPEN = "open"                       # needs a decision this tick
    SCHEDULED = "scheduled"             # deliberately waiting until next_action_at
    AWAITING_APPROVAL = "awaiting_approval"
    RECOVERED = "recovered"             # terminal, money in, ledger row exists
    STOPPED = "stopped"                 # terminal, a stopping rule fired
    ESCALATED = "escalated"             # terminal for the agent, handed to a human
    SUPPRESSED = "suppressed"           # terminal, must not be contacted at all
    #: The customer paid through another channel. Real money, but not ours to
    #: claim: it is counted separately and never reaches the recovery ledger.
    SETTLED_EXTERNALLY = "settled_externally"
    TERMINAL = (RECOVERED, STOPPED, ESCALATED, SUPPRESSED, SETTLED_EXTERNALLY)

    @classmethod
    def terminal_placeholders(cls) -> str:
        """`?,?,?...` sized to TERMINAL, so adding a state cannot silently break
        an IN clause somewhere else."""
        return ",".join("?" * len(cls.TERMINAL))


# ---------------------------------------------------------------------------
# Bounded autonomy. Every action carries a tier; the policy engine maps tier ->
# how much authority the agent has. The model proposes, the tier disposes.
# ---------------------------------------------------------------------------
class Tier:
    OBSERVE = 0        # record only, never touches the customer or money
    RECOMMEND = 1      # surfaced to the merchant, never auto-executed
    AUTO = 2           # executed autonomously inside policy limits
    APPROVAL = 3       # queued for explicit merchant approval before it can run
    FORBIDDEN = 4      # the agent may never execute this, with or without approval


ActionType = Literal[
    "no_action",
    "retry_payment",
    "send_recovery_link",
    "send_instrument_update_link",
    "send_mandate_reauth_link",
    "send_reminder",
    "offer_partial_payment",
    "escalate_to_merchant_ops",
    "escalate_to_collections",
    "open_engineering_ticket",
    "suppress_case",
    "issue_discount",
    "write_off",
]

#: The tier each action carries. This table, not the model, decides how much
#: authority any given action has.
ACTION_TIERS: dict[str, int] = {
    "no_action": Tier.OBSERVE,
    "suppress_case": Tier.OBSERVE,
    "retry_payment": Tier.AUTO,
    "send_recovery_link": Tier.AUTO,
    "send_instrument_update_link": Tier.AUTO,
    "send_mandate_reauth_link": Tier.AUTO,
    "send_reminder": Tier.AUTO,
    "escalate_to_merchant_ops": Tier.AUTO,
    "open_engineering_ticket": Tier.AUTO,
    "offer_partial_payment": Tier.APPROVAL,
    "escalate_to_collections": Tier.APPROVAL,
    "issue_discount": Tier.APPROVAL,
    # Writing revenue off is an accounting decision with tax consequences.
    # No autonomy tier makes that the agent's call.
    "write_off": Tier.FORBIDDEN,
}

#: Actions that put money at stake or reach a human. Used for exposure accounting.
#: Actions that re-present or alter money. Every one of these is subject to the
#: autonomous-amount ceiling. A retry's timing lives in `delay_hours`, not in a
#: second action name -- two names for one operation is how a bound gets bypassed.
MONEY_MOVING = frozenset({"retry_payment", "offer_partial_payment", "issue_discount"})
CUSTOMER_CONTACTING = frozenset(
    {
        "send_recovery_link",
        "send_instrument_update_link",
        "send_mandate_reauth_link",
        "send_reminder",
        "offer_partial_payment",
        "issue_discount",
        "escalate_to_collections",
    }
)

#: Closed set of root causes the diagnosis step may return.
ROOT_CAUSES = (
    "payer_balance",
    "instrument_dead",
    "issuer_outage",
    "psp_outage",
    "gateway_transient",
    "customer_abandoned",
    "limit_exhausted",
    "risk_decline",
    "mandate_invalid",
    "merchant_misconfiguration",
    "integration_defect",
    "already_paid",
    "payer_unwilling",
    "unknown",
)


@dataclass(slots=True)
class Diagnosis:
    """Output of the reasoning step. Deliberately small and fully enumerable."""

    root_cause: str
    confidence: float                      # 0..1
    recoverability: float                  # 0..1, expected chance money is recoverable at all
    rationale: str                         # short, structured -- not chain of thought
    evidence: list[str] = field(default_factory=list)
    reasoner: str = "heuristic"             # llm | heuristic
    model: str | None = None


@dataclass(slots=True)
class Plan:
    """A proposed intervention. Still just a proposal until policy clears it."""

    action_type: str
    params: dict
    delay_hours: float
    channel: str | None                    # email | sms | whatsapp | none
    message: str | None
    justification: str
    reasoner: str = "heuristic"

    @property
    def tier(self) -> int:
        return ACTION_TIERS.get(self.action_type, Tier.FORBIDDEN)


@dataclass(slots=True)
class RuleVerdict:
    rule: str
    passed: bool
    detail: str
    #: When a rule fails it can demand something stronger than the action's own
    #: tier: block it outright, or force it through merchant approval.
    escalate_to: str | None = None         # deny | require_approval


@dataclass(slots=True)
class PolicyDecision:
    decision: str                          # allow | require_approval | deny
    rules: list[RuleVerdict]
    stop_reason: str | None = None
    #: When set, the action was refused *for now* rather than forever: the case is
    #: rescheduled to this instant instead of being closed.
    reschedule_at: int | None = None

    @property
    def blocked_by(self) -> list[str]:
        return [r.rule for r in self.rules if not r.passed]
