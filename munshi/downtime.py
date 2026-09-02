"""Correlate a failure against Razorpay's Payment Downtime feed.

This is the signal that turns "retry in 6 hours" into a decision instead of a
default. Razorpay publishes downtime per instrument -- issuer, bank, UPI handle,
PSP, network -- with a severity and a status, over both the fetch API
(GET /v1/payments/downtimes) and the payment.downtime.* webhooks.

Retries are a bounded resource: policy caps them per case. Spending one on a
`bank_technical_error` while that exact issuer is in an active high-severity
outage is close to a guaranteed-zero action. Knowing the outage is live changes
the correct move from "retry now" to "hold until it clears" -- and knowing an
outage is *scheduled* changes it to "retry before the window opens".
"""

from __future__ import annotations

import sqlite3

from . import db
from .db import jload

#: Which instrument key identifies a downtime for each payment method.
MATCH_KEYS = {
    "card": ("issuer", "network"),
    "netbanking": ("bank",),
    "upi": ("vpa_handle", "psp"),
    "emandate": ("bank",),
}


def _matches(case_instrument: dict, method: str, dt_method: str, dt_instrument: dict) -> bool:
    if method != dt_method:
        return False
    keys = MATCH_KEYS.get(method, ())
    return any(
        k in dt_instrument and case_instrument.get(k) == dt_instrument[k] for k in keys
    )


def status_for(conn: sqlite3.Connection, case: dict, now: int) -> dict:
    """What Razorpay's downtime feed says about this case's instrument, right now."""
    method = case.get("method")
    instrument = jload(case.get("instrument"), {}) or {}
    if not method:
        return {"state": "not_applicable"}

    active, upcoming, recent = None, None, None
    for row in db.rows(conn, "SELECT * FROM downtimes"):
        dt_instrument = jload(row["instrument"], {})
        if not _matches(instrument, method, row["method"], dt_instrument):
            continue
        begin, end = row["begin_at"], row["end_at"]
        rec = {
            "id": row["id"], "severity": row["severity"], "status": row["status"],
            "scheduled": bool(row["scheduled"]), "begin_at": begin, "end_at": end,
            "instrument": dt_instrument,
        }
        ongoing = begin <= now and (end is None or end > now) and row["status"] != "resolved"
        if ongoing:
            active = rec
        elif begin > now:
            if upcoming is None or begin < upcoming["begin_at"]:
                upcoming = rec
        elif end is not None and now - end < 24 * 3600:
            recent = rec

    if active:
        return {
            "state": "active", "severity": active["severity"], "downtime": active,
            # An unscheduled outage has no published end. We cannot claim to know
            # when it clears, so we hold for a bounded window and re-check.
            "hold_hours": {"high": 4.0, "medium": 2.0, "low": 1.0}[active["severity"]],
            "note": f"Razorpay reports an active {active['severity']}-severity downtime "
                    f"for this {case['method']} instrument.",
        }
    if upcoming and upcoming["begin_at"] - now < 48 * 3600:
        return {"state": "scheduled", "downtime": upcoming,
                "note": "A scheduled downtime is coming for this instrument; avoid "
                        "placing a retry inside that window."}
    if recent:
        return {"state": "recently_resolved", "downtime": recent,
                "note": "A downtime on this instrument resolved within the last 24h; a "
                        "retry now is materially more likely to succeed than it was."}
    return {"state": "clear"}


def blocks_retry(status: dict) -> bool:
    """Active high/medium severity downtime makes a retry near-worthless."""
    return status.get("state") == "active" and status.get("severity") in ("high", "medium")


#: Longest an outage may hold a case before we stop waiting. An issuer that has
#: been down for this long is no longer a "wait it out" problem: the useful move
#: is to put a link in front of the customer so they can pay by another rail.
MAX_CONSECUTIVE_HOLDS = 3


def publish_resolutions(conn: sqlite3.Connection, now: int) -> int:
    """Stand in for the payment.downtime.resolved webhook.

    In production Razorpay pushes payment.downtime.started / .updated / .resolved
    and this table is updated from the webhook. In a batch run the same
    transition is driven off `resolves_at`, which is simulator ground truth the
    agent never reads -- so, exactly as in production, an unscheduled outage has
    no known end until the moment resolution is published.
    """
    cur = conn.execute(
        "UPDATE downtimes SET status='resolved', end_at=resolves_at"
        " WHERE status IN ('started','updated') AND resolves_at IS NOT NULL"
        " AND resolves_at <= ?",
        (now,),
    )
    return cur.rowcount or 0
