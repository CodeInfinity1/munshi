"""Deterministic synthetic batch of revenue-risk events.

Two design decisions carry the whole evaluation:

1. **Latent ground truth.** Every case gets a hidden `latent` record -- whether the
   money was ever recoverable, when the payer's balance actually tops up, whether
   the customer would really replace a dead card, when an outage really clears.
   The agent never sees it. The outcome oracle resolves actions against it. That
   is what makes "did the agent pick the right intervention?" a measurable
   question rather than a self-graded one.

2. **Fixed per-case seeds.** Agent and baseline run over identical cases with
   identical seeds, so the comparison is a genuine counterfactual rather than two
   draws from a random process.

Failure-reason mix is weighted to resemble a real Indian PG failure distribution
rather than a uniform spread, so the headline numbers are not flattered by an
unrealistically recoverable batch.
"""

from __future__ import annotations

import random
from datetime import datetime
from zoneinfo import ZoneInfo

from ..db import jdump

#: Batch reference time. Fixed so every run of the evaluation is reproducible.
BATCH_START = int(datetime(2026, 8, 24, 9, 0, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp())
HOUR = 3600
DAY = 86400

FIRST = [
    "Aarav", "Ananya", "Vihaan", "Diya", "Kabir", "Meera", "Rohan", "Ishita", "Arjun",
    "Saanvi", "Aditya", "Nisha", "Rahul", "Priya", "Karthik", "Divya", "Siddharth",
    "Neha", "Farhan", "Zoya", "Manav", "Tara", "Vikram", "Lakshmi", "Imran", "Sneha",
    "Aniket", "Pooja", "Yash", "Ritika", "Harsh", "Kavya", "Nikhil", "Aditi", "Suresh",
]
LAST = [
    "Sharma", "Iyer", "Patel", "Reddy", "Nair", "Gupta", "Menon", "Bose", "Khan",
    "Deshpande", "Rao", "Joshi", "Chatterjee", "Pillai", "Mehta", "Kulkarni", "Banerjee",
    "Shetty", "Verma", "Ahuja",
]
COMPANIES = [
    "Kirana Junction", "Sunrise Diagnostics", "Bluewave Logistics", "Tatva Interiors",
    "Meridian EdTech", "Anantham Textiles", "Fulcrum Analytics", "Greenline Foods",
    "Nimbus Cloudworks", "Vector Fabrication", "Saffron Hospitality", "Kaveri Agritech",
    "Deccan Print House", "Orbit Fintech", "Palladium Realty",
]

BANKS = ["HDFC", "SBIN", "ICIC", "UTIB", "KKBK", "PUNB", "BARB", "YESB", "IDFB"]
VPA_HANDLES = ["okhdfcbank", "oksbi", "okicici", "okaxis", "ybl", "paytm", "ibl", "apl"]
NETWORKS = ["VISA", "MasterCard", "RuPay", "Amex"]
ISSUERS = ["HDFC", "SBIN", "ICIC", "UTIB", "KKBK", "AMEX"]

# (reason, weight). Weights approximate a real Indian PG failure mix: balance and
# customer-dropout dominate, hard-dead instruments and risk declines are the long tail.
REASONS_ONE_TIME = [
    ("insufficient_funds", 16), ("payment_timed_out", 11), ("payment_cancelled", 9),
    ("authentication_failed", 7), ("bank_technical_error", 7), ("payment_failed", 6),
    ("incorrect_otp", 5), ("gateway_technical_error", 4), ("issuer_technical_error", 4),
    ("card_expired", 4), ("invalid_vpa", 4), ("transaction_daily_limit_exceeded", 3),
    ("psp_app_not_available", 3), ("debit_instrument_blocked", 3), ("card_declined", 3),
    ("payment_risk_check_failed", 3), ("transaction_frequency_limit_exceeded", 2),
    ("payment_session_expired", 2), ("order_already_paid", 2),
    ("payment_method_not_enabled", 1), ("invalid_order_id", 1), ("server_error", 1),
    ("vpa_resolution_failed", 1), ("transaction_on_vpa_restricted", 1),
]
REASONS_SUBSCRIPTION = [
    ("insufficient_funds", 26), ("card_expired", 11), ("bank_technical_error", 8),
    ("debit_instrument_blocked", 7), ("mandate_creation_failed", 6),
    ("transaction_limit_exceeded", 5), ("issuer_technical_error", 5),
    ("payment_failed", 5), ("funds_blocked_by_mandate", 4), ("card_declined", 4),
    ("mandate_creation_expired", 3), ("reqauth_mandate_not_acknowledged", 3),
    ("recurring_payment_not_enabled", 2), ("payment_risk_check_failed", 2),
    ("transaction_daily_limit_exceeded", 2), ("upi_autopay_not_supported_on_psp", 2),
    ("credit_limit_exceeded", 2), ("server_error", 1), ("order_already_paid", 1),
]

# Latent recoverability priors by taxonomy family. These say: given perfect play,
# what fraction of this money was ever collectable?
FAMILY_RECOVERABLE = {
    "balance_dependent": 0.72,
    "customer_dropout": 0.68,
    "transient_infra": 0.78,
    "limit_bound": 0.61,
    "instrument_dead": 0.44,   # only if the customer actually re-attaches an instrument
    "mandate_broken": 0.40,
    "merchant_config": 0.55,   # recoverable, but only after the merchant fixes config
    "integration_bug": 0.50,
    "risk_flagged": 0.05,      # essentially uncollectable, and must not be chased
    "already_settled": 0.0,    # already in the bank; nothing to recover
}

CAUSE_BY_FAMILY = {
    "balance_dependent": "payer_balance",
    "customer_dropout": "customer_abandoned",
    "transient_infra": "gateway_transient",
    "limit_bound": "limit_exhausted",
    "instrument_dead": "instrument_dead",
    "mandate_broken": "mandate_invalid",
    "merchant_config": "merchant_misconfiguration",
    "integration_bug": "integration_defect",
    "risk_flagged": "risk_decline",
    "already_settled": "already_paid",
}


def _weighted(rng: random.Random, table: list[tuple[str, int]]) -> str:
    total = sum(w for _, w in table)
    x = rng.uniform(0, total)
    for name, w in table:
        x -= w
        if x <= 0:
            return name
    return table[-1][0]


def _instrument(rng: random.Random, method: str) -> dict:
    if method == "upi":
        return {"vpa_handle": rng.choice(VPA_HANDLES), "psp": rng.choice(["gpay", "phonepe", "paytm", "bhim"])}
    if method == "card":
        return {"issuer": rng.choice(ISSUERS), "network": rng.choice(NETWORKS),
                "card_type": rng.choice(["credit", "debit"]), "last4": f"{rng.randint(1000, 9999)}"}
    if method == "netbanking":
        return {"bank": rng.choice(BANKS)}
    return {"bank": rng.choice(BANKS), "mandate_type": rng.choice(["emandate", "upi_autopay"])}


def _downtimes(rng: random.Random) -> list[dict]:
    """A handful of Razorpay-shaped downtime windows overlapping the batch.

    These exist so that "is this instrument down right now?" is a real question
    with a real answer, not a decoration. Shape matches the Payment Downtime
    entity: method / instrument / begin / end / status / severity / scheduled.
    """
    return [
        {"id": "down_SIM_hdfc_nb", "method": "netbanking", "instrument": {"bank": "HDFC"},
         "begin_at": BATCH_START - 5 * DAY, "end_at": BATCH_START - 5 * DAY + 7 * HOUR,
         "status": "resolved", "severity": "high", "scheduled": 0, "resolves_at": None},
        # Unscheduled outages: no published end. The merchant only learns they are
        # over when payment.downtime.resolved fires, which is what resolves_at models.
        {"id": "down_SIM_ybl_upi", "method": "upi", "instrument": {"vpa_handle": "ybl"},
         "begin_at": BATCH_START - 2 * HOUR, "end_at": None,
         "status": "started", "severity": "high", "scheduled": 0,
         "resolves_at": BATCH_START + 9 * HOUR},
        {"id": "down_SIM_sbin_card", "method": "card", "instrument": {"issuer": "SBIN", "card_type": "credit"},
         "begin_at": BATCH_START - 30 * 60, "end_at": None,
         "status": "started", "severity": "medium", "scheduled": 0,
         "resolves_at": BATCH_START + 5 * HOUR},
        {"id": "down_SIM_icic_nb", "method": "netbanking", "instrument": {"bank": "ICIC"},
         "begin_at": BATCH_START + 2 * DAY, "end_at": BATCH_START + 2 * DAY + 4 * HOUR,
         "status": "scheduled", "severity": "high", "scheduled": 1, "resolves_at": None},
    ]


def _customer(rng: random.Random, idx: int) -> dict:
    segment = _weighted(rng, [("consumer", 60), ("smb", 30), ("enterprise", 10)])
    if segment == "consumer":
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        lifetime = rng.randint(1_500, 240_000) * 100
    else:
        name = rng.choice(COMPANIES)
        lifetime = rng.randint(40_000, 4_000_000) * 100
    successes = rng.randint(0, 46)
    handle = name.lower().replace(" ", ".")
    return {
        "id": f"cust_{idx:04d}",
        "name": name,
        "email": f"{handle}@example.com",
        "phone": f"+9198{rng.randint(10_000_000, 99_999_999)}",
        "segment": segment,
        "tenure_days": rng.randint(3, 1600),
        "lifetime_paise": lifetime,
        "successful_payments": successes,
        "failed_payments": rng.randint(0, 6),
        "prior_recoveries": rng.randint(0, 3) if successes > 4 else 0,
        # ~4% of the base has opted out of contact. Contacting them is a hard stop.
        "contact_opt_out": 1 if rng.random() < 0.04 else 0,
        "preferred_channel": _weighted(rng, [("email", 45), ("sms", 30), ("whatsapp", 25)]),
        "typical_success_hour": rng.choice([9, 10, 11, 14, 15, 18, 19, 20, 21]),
    }


def _latent(rng: random.Random, family: str, cust: dict, kind: str) -> dict:
    """Hidden truth. The agent never reads this; only the outcome oracle does."""
    base = FAMILY_RECOVERABLE[family]
    # Long-tenure customers with a track record really are more collectable.
    loyalty = min(0.18, 0.012 * cust["successful_payments"])
    if cust["contact_opt_out"]:
        loyalty -= 0.05
    recoverable = rng.random() < min(0.95, base + loyalty)

    lat = {
        "true_cause": CAUSE_BY_FAMILY[family],
        "family": family,
        "recoverable": recoverable,
        "intent": _weighted(rng, [("warm", 35), ("lukewarm", 40), ("cold", 25)]),
        "responds_to_contact": round(rng.uniform(0.15, 0.85), 3),
        "seed": rng.randint(1, 2**31 - 1),
    }
    if family == "balance_dependent":
        # When money actually lands in the payer's account. Salary-cycle-ish.
        lat["funds_available_after_h"] = rng.choice([8, 14, 22, 30, 38, 50, 62, 74, 96, 140])
    if family == "instrument_dead":
        lat["will_replace_instrument"] = recoverable and rng.random() < 0.75
    if family == "transient_infra":
        lat["outage_clears_after_h"] = rng.choice([0.5, 1, 2, 3, 5, 8, 14, 26])
    if family == "limit_bound":
        lat["limit_resets_after_h"] = rng.choice([12, 18, 24, 30])
    if family == "merchant_config":
        lat["merchant_fixes_after_h"] = rng.choice([6, 20, 48, 96])
    if family == "already_settled":
        lat["already_paid"] = True
    if kind == "invoice_overdue":
        lat["will_promise_to_pay"] = rng.random() < 0.45
        lat["honours_promise"] = rng.random() < 0.62
    # The race every real recovery system has to survive: the customer pays
    # through another channel while we are mid-workflow. Chasing them after that
    # is the same false positive as chasing someone Razorpay already told us
    # had paid -- it is just harder to notice.
    if recoverable and family not in ("already_settled", "risk_flagged") and rng.random() < 0.07:
        lat["settles_externally_after_h"] = rng.choice([6, 18, 30, 54, 90, 150])
    return lat


def build(n: int = 320, seed: int = 20260824) -> dict:
    """Generate the batch. Pure function of (n, seed)."""
    from ..taxonomy import lookup

    rng = random.Random(seed)
    customers = [_customer(rng, i) for i in range(1, int(n * 0.8) + 1)]
    cases: list[dict] = []
    events: list[dict] = []

    for i in range(1, n + 1):
        cust = rng.choice(customers)
        kind = _weighted(rng, [
            ("payment_failure", 38), ("subscription_failure", 30),
            ("invoice_overdue", 20), ("checkout_abandoned", 12),
        ])
        case_id = f"case_{i:04d}"
        opened_at = BATCH_START - rng.randint(1, 12 * DAY)
        amount = _amount(rng, cust["segment"], kind)

        if kind == "checkout_abandoned":
            method, instrument, reason = None, {}, None
            family = "customer_dropout"
            error_source = error_step = None
        elif kind == "invoice_overdue":
            method, instrument, reason = None, {}, None
            family = "balance_dependent"
            error_source = error_step = None
        else:
            table = REASONS_SUBSCRIPTION if kind == "subscription_failure" else REASONS_ONE_TIME
            reason = _weighted(rng, table)
            sem = lookup(reason)
            family = sem.family
            error_source = rng.choice(sem.sources)
            error_step = _step_for(rng, error_source)
            method = _method_for(rng, kind, family)
            instrument = _instrument(rng, method)
            # Make downtime correlation real: some failures genuinely sit inside
            # a live outage window for their instrument.
            if family == "transient_infra" and rng.random() < 0.45:
                method, instrument = rng.choice([
                    ("upi", {"vpa_handle": "ybl", "psp": "phonepe"}),
                    ("card", {"issuer": "SBIN", "network": "VISA", "card_type": "credit",
                              "last4": f"{rng.randint(1000, 9999)}"}),
                ])
                opened_at = BATCH_START - rng.randint(10 * 60, 90 * 60)

        entity_prefix = {"payment_failure": "pay", "subscription_failure": "sub",
                         "invoice_overdue": "inv", "checkout_abandoned": "cart"}[kind]
        entity_id = f"{entity_prefix}_{rng.getrandbits(48):012X}"
        days_overdue = rng.randint(3, 95) if kind == "invoice_overdue" else 0
        mrr = amount if kind == "subscription_failure" else 0

        cases.append({
            "id": case_id, "kind": kind, "entity_id": entity_id, "customer_id": cust["id"],
            "amount_paise": amount, "currency": "INR", "opened_at": opened_at,
            "method": method, "instrument": jdump(instrument), "error_source": error_source,
            "error_step": error_step, "error_reason": reason,
            "prior_attempts": rng.choice([0, 0, 0, 1, 1, 2]) if kind != "invoice_overdue" else 0,
            "days_overdue": days_overdue, "mrr_paise": mrr,
            "latent": jdump(_latent(rng, family, cust, kind)),
        })
        events.append(_event(rng, kind, entity_id, cust["id"], opened_at, amount,
                             reason, error_source, error_step, method, instrument))

    # A real webhook stream redelivers. Replay ~3% of events verbatim so the
    # idempotency path is exercised by the batch itself, not only by a unit test.
    replays = [dict(e) for e in rng.sample(events, k=max(1, int(len(events) * 0.03)))]
    return {
        "meta": {"n": n, "seed": seed, "batch_start": BATCH_START,
                 "replayed_events": len(replays)},
        "customers": customers,
        "cases": cases,
        "events": events + replays,
        "downtimes": _downtimes(rng),
    }


def _amount(rng: random.Random, segment: str, kind: str) -> int:
    if segment == "enterprise":
        rupees = rng.randint(45_000, 1_250_000)
    elif segment == "smb":
        rupees = rng.randint(1_800, 90_000)
    else:
        rupees = rng.choice([199, 249, 299, 399, 499, 599, 799, 999, 1499, 1999, 2999, 4999])
    if kind == "subscription_failure" and segment == "consumer":
        rupees = rng.choice([149, 199, 299, 499, 799, 1299])
    return rupees * 100


def _method_for(rng: random.Random, kind: str, family: str) -> str:
    if kind == "subscription_failure":
        return _weighted(rng, [("emandate", 55), ("card", 30), ("upi", 15)])
    return _weighted(rng, [("upi", 45), ("card", 35), ("netbanking", 20)])


def _step_for(rng: random.Random, source: str) -> str:
    return {
        "customer": rng.choice(["payment_authentication", "payment_initiation"]),
        "business": "payment_initiation",
        "gateway": rng.choice(["payment_authorization", "payment_authentication"]),
        "razorpay": "payment_initiation",
    }[source]


def _event(rng, kind, entity_id, customer_id, ts, amount, reason, source, step, method, instrument):
    """Shaped like the Razorpay webhook payloads these cases would arrive as."""
    kind_to_event = {
        "payment_failure": "payment.failed",
        "subscription_failure": "subscription.charged.failed",
        "invoice_overdue": "invoice.expired",
        "checkout_abandoned": "order.abandoned",
    }
    payload: dict = {"entity_id": entity_id, "amount": amount, "currency": "INR",
                     "method": method, "instrument": instrument}
    if reason:
        payload["error"] = {
            "code": "BAD_REQUEST_ERROR" if source in ("customer", "business") else "GATEWAY_ERROR",
            "source": source, "step": step, "reason": reason,
        }
    return {
        "id": f"evt_{rng.getrandbits(64):016X}",
        "kind": kind_to_event[kind],
        "entity_id": entity_id,
        "customer_id": customer_id,
        "occurred_at": ts,
        "payload": payload,
    }
