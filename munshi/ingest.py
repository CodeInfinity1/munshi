"""Event ingestion.

Webhook deliveries are at-least-once: Razorpay retries until you 2xx, and network
timeouts mean you will see the same event twice. Every ingest is keyed on the
provider event id, so a redelivery is recorded as a duplicate and does not
produce a second recovery attempt. This is the difference between "we handle
retries" and "we double-charge a customer during an incident".
"""

from __future__ import annotations

import sqlite3
import time

from . import db
from .db import jdump

#: Event kinds Munshi acts on. Anything else is stored and ignored.
ACTIONABLE = {
    "payment.failed": "payment_failure",
    "subscription.charged.failed": "subscription_failure",
    "subscription.halted": "subscription_failure",
    "invoice.expired": "invoice_overdue",
    "order.abandoned": "checkout_abandoned",
    "payment_link.expired": "invoice_overdue",
}


def ingest_event(conn: sqlite3.Connection, event: dict, now: int | None = None) -> dict:
    """Idempotent. Returns {'status': 'accepted'|'duplicate'|'ignored'}."""
    now = now or int(time.time())
    eid = event["id"]
    existing = db.one(conn, "SELECT id FROM events WHERE id = ?", (eid,))
    if existing:
        return {"status": "duplicate", "event_id": eid}

    kind = event["kind"]
    conn.execute(
        "INSERT INTO events (id,kind,entity_id,customer_id,occurred_at,received_at,payload)"
        " VALUES (?,?,?,?,?,?,?)",
        (eid, kind, event["entity_id"], event["customer_id"], event["occurred_at"], now,
         jdump(event.get("payload", {}))),
    )
    if kind not in ACTIONABLE:
        return {"status": "ignored", "event_id": eid, "reason": f"unhandled kind {kind}"}
    return {"status": "accepted", "event_id": eid, "case_kind": ACTIONABLE[kind]}


def ingest_batch(conn: sqlite3.Connection, events: list[dict], now: int | None = None) -> dict:
    accepted = duplicates = ignored = 0
    with db.transaction(conn):
        for e in events:
            r = ingest_event(conn, e, now=now)
            accepted += r["status"] == "accepted"
            duplicates += r["status"] == "duplicate"
            ignored += r["status"] == "ignored"
    return {"ingested": accepted, "duplicates": duplicates, "ignored": ignored}
