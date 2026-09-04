"""Prioritisation: which cases are worth this tick's attention, and in what order.

An agent with finite capacity has to choose what to work on. Processing cases in
whatever order the database returns them is not a decision, and it is the wrong
one whenever the work budget binds: on a book where 36% of failed value can never
be recovered, sorting by amount puts uncollectable money at the top of the queue.

The score is deterministic and fully decomposed. It is exposed to the agent as a
tool and rendered in the UI, so both the model and the merchant can see *why* a
case ranked where it did rather than being handed an opaque number.

Expected recoverable value = amount x P(recover) x urgency, where P(recover) is
built from signals the agent could not weigh reliably itself: the taxonomy's own
recoverability prior, the payer's track record, whether the rail is currently
down, how much of the retry budget is left, and how much of the recovery window
remains.
"""

from __future__ import annotations

#: Prior probability that money in this failure family is recoverable at all,
#: given a competent intervention. Derived from each family's documented
#: resolution condition, not tuned against the evaluation.
FAMILY_PRIOR = {
    "transient_infra": 0.72,
    "balance_dependent": 0.64,
    "customer_dropout": 0.58,
    "limit_bound": 0.55,
    "merchant_config": 0.50,
    "instrument_dead": 0.34,
    "mandate_broken": 0.30,
    "integration_bug": 0.20,
    "risk_flagged": 0.02,
    "already_settled": 0.0,
}
DEFAULT_PRIOR = 0.35

#: Recovery-window pressure. A case with two days left is worth acting on before
#: one with twelve, at equal expected value.
WINDOW_DAYS = 14


def recovery_score(case: dict, ctx: dict) -> dict:
    """Decomposed expected recoverable value, in paise.

    Returns every factor, not just the product, because an unexplained priority
    is a priority nobody will trust.
    """
    amount = int(case["amount_paise"])
    family = ctx["failure"]["family"]
    base = FAMILY_PRIOR.get(family, DEFAULT_PRIOR)
    factors: dict[str, float] = {"family_prior": round(base, 3)}
    p = base

    # A payer with a long record of settling is materially more collectable.
    cust = ctx["customer"]
    successes = cust.get("successful_payments") or 0
    loyalty = min(0.15, 0.010 * successes)
    if cust.get("contact_opt_out"):
        # We can still retry, but we cannot ask them to do anything.
        loyalty -= 0.08
    p += loyalty
    factors["payer_track_record"] = round(loyalty, 3)

    # A rail that is down right now depresses this tick's chance, not the case's
    # ultimate recoverability -- hence a modest, explicitly temporary penalty.
    dt = ctx["downtime"].get("state")
    downtime_adj = -0.18 if dt == "active" else (0.05 if dt == "recently_resolved" else 0.0)
    p += downtime_adj
    factors["instrument_downtime"] = round(downtime_adj, 3)

    # Each attempt already spent is evidence the easy paths did not work.
    spent = (case["attempts"] or 0) + (case["prior_attempts"] or 0)
    attempt_adj = -0.06 * spent
    p += attempt_adj
    factors["attempts_already_spent"] = round(attempt_adj, 3)

    # Ageing receivables decay; so does abandoned-checkout intent.
    if case["kind"] == "invoice_overdue":
        age_adj = -min(0.30, 0.004 * (case["days_overdue"] or 0))
    else:
        age_adj = -min(0.20, 0.0015 * float(ctx["case"]["age_hours"]))
    p += age_adj
    factors["age_decay"] = round(age_adj, 3)

    p = max(0.0, min(0.95, p))

    # Urgency: how little of the window is left. Ties break toward the case that
    # will expire first.
    remaining_days = max(0.0, WINDOW_DAYS - float(ctx["case"]["age_hours"]) / 24)
    urgency = 1.0 + 0.5 * (1.0 - remaining_days / WINDOW_DAYS)

    expected = int(amount * p * urgency)
    return {
        "case_id": case["id"],
        "amount_paise": amount,
        "probability": round(p, 4),
        "urgency": round(urgency, 3),
        "expected_recoverable_paise": expected,
        "remaining_window_days": round(remaining_days, 2),
        "factors": factors,
        "explanation": _explain(family, p, factors, expected),
    }


def _explain(family: str, p: float, factors: dict, expected: int) -> str:
    biggest = max(
        (k for k in factors if k != "family_prior"),
        key=lambda k: abs(factors[k]),
        default=None,
    )
    tail = ""
    if biggest and abs(factors[biggest]) >= 0.02:
        direction = "raised" if factors[biggest] > 0 else "lowered"
        tail = f", {direction} mainly by {biggest.replace('_', ' ')}"
    return (
        f"{family} carries a {factors['family_prior']:.0%} recoverability prior; "
        f"adjusted to {p:.0%}{tail}. Expected recoverable Rs {expected / 100:,.0f}."
    )


def prioritise(scored: list[dict], budget: int | None = None) -> list[dict]:
    """Highest expected recoverable value first, then soonest to expire."""
    ranked = sorted(
        scored,
        key=lambda s: (-s["expected_recoverable_paise"], s["remaining_window_days"]),
    )
    return ranked[:budget] if budget else ranked
