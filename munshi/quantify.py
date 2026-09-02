"""How much money is actually at stake.

Deliberately split into two numbers that are never added together:

- `at_risk_paise`  -- the invoice/charge in front of us. This is the only figure
  that rolls up into the headline "revenue at risk", because it is the only one
  that can be *recovered* by an intervention today.
- `annualised_mrr_paise` -- for a failed subscription charge, the recurring value
  that walks out of the door if the mandate is never repaired. Real, material,
  and reported separately, because folding it into the headline would let a
  single failed Rs 499 charge masquerade as Rs 5,988 of "recovered revenue".

Involuntary churn is the reason the second number matters: a subscription that
dies on a failed charge takes its whole future with it. Conflating the two is
the most common way revenue-recovery tooling overstates its own impact.
"""

from __future__ import annotations

from .models import CaseKind

#: Probability a subscription is lost outright if the failed charge is never
#: recovered. Used only for the separately-reported churn exposure figure.
CHURN_IF_UNRECOVERED = 0.62


def quantify(case: dict) -> dict:
    amount = int(case["amount_paise"])
    mrr = int(case.get("mrr_paise") or 0)
    annualised = mrr * 12
    churn_exposure = int(annualised * CHURN_IF_UNRECOVERED) if mrr else 0

    # Recovery is worth doing when the money at stake clears the cost of chasing.
    # A Rs 199 consumer charge does not justify a collections escalation.
    return {
        "at_risk_paise": amount,
        "annualised_mrr_paise": annualised,
        "churn_exposure_paise": churn_exposure,
        "is_recurring": bool(mrr),
        "materiality": _materiality(amount, case["kind"]),
    }


def _materiality(amount_paise: int, kind: str) -> str:
    """Coarse band that gates how much effort an intervention may cost."""
    rupees = amount_paise / 100
    if kind == CaseKind.INVOICE_OVERDUE and rupees >= 50_000:
        return "high"
    if rupees >= 25_000:
        return "high"
    if rupees >= 2_000:
        return "medium"
    return "low"


def total_at_risk(cases: list[dict]) -> int:
    return sum(int(c["amount_paise"]) for c in cases)
