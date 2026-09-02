"""Tamper-evidence and at-least-once delivery."""

from munshi import audit, db
from munshi.ingest import ingest_batch, ingest_event

EVENT = {"id": "evt_1", "kind": "payment.failed", "entity_id": "pay_1",
         "customer_id": "cust_1", "occurred_at": 100, "payload": {"amount": 1000}}


def test_audit_chain_verifies_when_untouched(conn):
    for i in range(20):
        audit.record(conn, ts=i, stage="policy", summary=f"s{i}", detail={"i": i})
    out = audit.verify(conn)
    assert out["valid"] and out["checked"] == 20


def test_editing_an_audit_row_breaks_the_chain(conn):
    for i in range(5):
        audit.record(conn, ts=i, stage="execute", summary=f"s{i}", detail={"i": i})
    conn.execute("UPDATE audit SET summary='money was never sent' WHERE seq=3")
    out = audit.verify(conn)
    assert not out["valid"] and out["broken_at"] == 3


def test_deleting_an_audit_row_breaks_the_chain(conn):
    for i in range(5):
        audit.record(conn, ts=i, stage="execute", summary=f"s{i}", detail={"i": i})
    conn.execute("DELETE FROM audit WHERE seq=2")
    assert not audit.verify(conn)["valid"]


def test_recovered_amount_cannot_be_quietly_edited(conn):
    audit.record(conn, ts=1, stage="verify", summary="recovered Rs 100",
                 detail={"amount_paise": 10000})
    conn.execute("UPDATE audit SET detail='{\"amount_paise\":9999999}' WHERE seq=1")
    assert not audit.verify(conn)["valid"]


def test_duplicate_webhook_delivery_is_rejected(conn):
    assert ingest_event(conn, EVENT)["status"] == "accepted"
    assert ingest_event(conn, EVENT)["status"] == "duplicate"
    assert db.scalar(conn, "SELECT COUNT(*) FROM events") == 1


def test_batch_ingest_counts_replays(conn):
    out = ingest_batch(conn, [EVENT, dict(EVENT), {**EVENT, "id": "evt_2"}])
    assert out == {"ingested": 2, "duplicates": 1, "ignored": 0}


def test_unhandled_event_kinds_are_stored_but_not_acted_on(conn):
    out = ingest_event(conn, {**EVENT, "id": "evt_9", "kind": "payment.captured"})
    assert out["status"] == "ignored"
    assert db.scalar(conn, "SELECT COUNT(*) FROM events WHERE id='evt_9'") == 1
