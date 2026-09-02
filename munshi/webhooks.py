"""Razorpay webhook receiver.

Three properties this endpoint must have, in order:

1. **Verify before parsing.** The signature covers the raw request bytes. Parsing
   the JSON and re-serialising it changes the bytes and the signature will never
   match again, so verification runs on `await request.body()` before anything
   else touches it.
2. **Reject unsigned traffic outright.** If no webhook secret is configured, this
   endpoint refuses everything rather than accepting unverified events. An
   unauthenticated write path into a system that moves money is worse than no
   webhook at all.
3. **Be idempotent.** Razorpay retries until it gets a 2xx. A redelivered event is
   recorded as a duplicate and returns 200 -- because returning an error would
   just make Razorpay send it again.
"""

from __future__ import annotations

import sqlite3
import time

from .adapters.razorpay_test import normalise_downtime, verify_webhook
from .config import settings
from .db import jdump
from .ingest import ingest_event

#: Razorpay event -> the internal event kind ingest understands.
EVENT_MAP = {
    "payment.failed": "payment.failed",
    "subscription.charged.failed": "subscription.charged.failed",
    "subscription.halted": "subscription.halted",
    "invoice.expired": "invoice.expired",
    "payment_link.expired": "payment_link.expired",
    "order.paid": "order.paid",
    "payment.captured": "payment.captured",
}
DOWNTIME_EVENTS = {"payment.downtime.started", "payment.downtime.updated",
                   "payment.downtime.resolved"}


def handle(conn: sqlite3.Connection, body: bytes, signature: str, payload: dict) -> dict:
    """Verify, then route. Returns the response body; raises on bad signature."""
    if not settings().razorpay_webhook_secret:
        raise PermissionError(
            "RAZORPAY_WEBHOOK_SECRET is not configured; refusing to accept unverified "
            "webhook traffic into a system that moves money"
        )
    if not verify_webhook(body, signature):
        raise PermissionError("X-Razorpay-Signature did not match the request body")

    event = payload.get("event", "")
    now = int(time.time())

    if event in DOWNTIME_EVENTS:
        return _downtime(conn, payload, event)
    if event not in EVENT_MAP:
        return {"status": "ignored", "event": event}

    entity = _entity(payload)
    result = ingest_event(conn, {
        # Razorpay's own event id makes redelivery detection free.
        "id": payload.get("id") or f"evt_{now}_{entity.get('id', 'unknown')}",
        "kind": EVENT_MAP[event],
        "entity_id": entity.get("id", "unknown"),
        "customer_id": entity.get("customer_id") or entity.get("notes", {}).get(
            "munshi_customer_id", "unknown"),
        "occurred_at": payload.get("created_at", now),
        "payload": entity,
    }, now=now)
    return {"status": result["status"], "event": event}


def _downtime(conn, payload: dict, event: str) -> dict:
    """payment.downtime.* keeps the instrument outage table current."""
    item = payload.get("payload", {}).get("payment.downtime", {}).get("entity", {})
    if not item.get("id"):
        return {"status": "ignored", "reason": "no downtime entity in payload"}
    d = normalise_downtime(item)
    conn.execute(
        "INSERT INTO downtimes (id,method,instrument,begin_at,end_at,status,severity,scheduled)"
        " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET"
        " status=excluded.status, end_at=excluded.end_at, severity=excluded.severity",
        (d["id"], d["method"], jdump(d["instrument"]), d["begin_at"], d["end_at"],
         d["status"], d["severity"], d["scheduled"]),
    )
    return {"status": "recorded", "event": event, "downtime_id": d["id"]}


def _entity(payload: dict) -> dict:
    """Razorpay nests the entity under payload.<entity_name>.entity."""
    for holder in payload.get("payload", {}).values():
        if isinstance(holder, dict) and "entity" in holder:
            return holder["entity"]
    return {}
