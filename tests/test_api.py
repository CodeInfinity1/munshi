"""API surface: auth on every write, HMAC on the webhook, no ground truth served."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from munshi.config import settings

SECRET = "whsec_test"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MUNSHI_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("MUNSHI_API_TOKEN", "test-token")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)
    settings.cache_clear()
    from munshi import db
    from munshi.seed.load import load

    c = db.reset(tmp_path / "api.db")
    load(c, n=30, seed=4)
    c.close()
    import munshi.api as api

    yield TestClient(api.app)
    settings.cache_clear()


AUTH = {"Authorization": "Bearer test-token"}


def test_health_states_plainly_whether_money_movement_is_simulated(client):
    body = client.get("/api/health").json()
    assert body["money_movement"] == "simulated"
    assert body["adapter"] == "simulator"
    assert body["reasoner"] in ("llm", "heuristic")


def test_write_routes_require_a_token(client):
    for method, path in [("post", "/api/run"), ("post", "/api/seed"),
                         ("post", "/api/approvals/act_x/approve")]:
        assert getattr(client, method)(path, json={}).status_code == 401


def test_wrong_token_is_rejected(client):
    r = client.post("/api/run", json={}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_latent_ground_truth_is_never_served(client):
    """The API must not leak what the agent itself is not allowed to see."""
    listing = client.get("/api/cases").json()
    assert listing["cases"]
    for case in listing["cases"]:
        assert "latent" not in case
    detail = client.get(f"/api/cases/{listing['cases'][0]['id']}").json()
    assert detail["case"]["customer_name"], "detail must carry the customer name"
    blob = json.dumps(detail)
    for secret in ("funds_available_after_h", "will_replace_instrument", "true_cause",
                   "responds_to_contact", "outage_clears_after_h"):
        assert secret not in blob, secret


def test_policy_route_publishes_the_bounds(client):
    p = client.get("/api/policy").json()
    assert p["limits"]["max_recovery_attempts"] == 3
    assert "write_off" in p["tiers"]["L4"]["actions"]
    assert p["regulatory"]["rbi_emandate_afa_free_ceiling_inr"] == 15000
    assert "08:00-19:00" in p["regulatory"]["rbi_fair_practices_contact_window"]


def test_unknown_case_is_404(client):
    assert client.get("/api/cases/case_does_not_exist").status_code == 404


def test_webhook_rejects_a_bad_signature(client):
    body = json.dumps({"event": "payment.failed"}).encode()
    r = client.post("/webhooks/razorpay", content=body,
                    headers={"X-Razorpay-Signature": "0" * 64})
    assert r.status_code == 401


def test_webhook_rejects_an_absent_signature(client):
    r = client.post("/webhooks/razorpay", content=b'{"event":"payment.failed"}')
    assert r.status_code == 401


def _signed(payload: dict):
    body = json.dumps(payload).encode()
    sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-Razorpay-Signature": sig}


def test_valid_webhook_is_accepted_and_replays_return_200(client):
    body, headers = _signed({
        "event": "payment.failed", "id": "evt_wh_1", "created_at": 1787540000,
        "payload": {"payment": {"entity": {"id": "pay_WH1", "customer_id": "cust_0001",
                                           "amount": 50000}}},
    })
    first = client.post("/webhooks/razorpay", content=body, headers=headers)
    second = client.post("/webhooks/razorpay", content=body, headers=headers)
    assert first.status_code == 200 and first.json()["status"] == "accepted"
    # A non-2xx here would make Razorpay redeliver something already handled.
    assert second.status_code == 200 and second.json()["status"] == "duplicate"


def test_downtime_webhook_updates_the_outage_table(client):
    body, headers = _signed({
        "event": "payment.downtime.started", "id": "evt_dt_1",
        "payload": {"payment.downtime": {"entity": {
            "id": "down_WH1", "method": "upi", "instrument": {"vpa_handle": "okhdfcbank"},
            "begin": 1787540000, "end": None, "status": "started", "severity": "high",
            "scheduled": False}}},
    })
    r = client.post("/webhooks/razorpay", content=body, headers=headers)
    assert r.status_code == 200 and r.json()["downtime_id"] == "down_WH1"

    from munshi import db

    c = db.connect()
    assert db.scalar(c, "SELECT COUNT(*) FROM downtimes WHERE id='down_WH1'") == 1
    c.close()


def test_overview_reports_audit_verification(client):
    body = client.get("/api/overview").json()
    assert body["audit"]["valid"] is True
    assert body["money"]["at_risk_paise"] > 0
    assert body["config"]["adapter"] == "simulator"


def test_razorpay_status_reports_no_call_when_there_are_no_keys(client):
    """The integration panel must never imply a call it did not make."""
    body = client.get("/api/razorpay").json()
    assert body["credentials_present"] is False
    assert body["adapter"] == "simulator"
    assert body["live_call"] is None
    assert "No Razorpay call was made" in body["note"]


def test_downtime_sync_refuses_without_the_live_adapter(client):
    r = client.post("/api/razorpay/sync-downtimes", headers=AUTH)
    assert r.status_code == 400
    assert "razorpay_test" in r.json()["detail"]


def test_every_escalation_has_an_owner_and_a_next_action(client):
    """An escalation with no owner is a dead end dressed up as a handoff."""
    from munshi import db

    c = db.connect()
    c.execute("UPDATE cases SET state='escalated',"
              " stop_reason='risk_decline_requires_human_review'"
              " WHERE id = (SELECT id FROM cases LIMIT 1)")
    c.close()
    body = client.get("/api/escalations").json()
    assert body["total_cases"] >= 1
    for g in body["groups"]:
        assert g["owner"] and g["next_action"]
        assert g["value_paise"] > 0


def test_policy_publishes_the_agent_tool_surface(client):
    tools = client.get("/api/policy").json()["agent_tools"]
    names = {t["name"] for t in tools}
    assert "submit_decision" in names
    assert not (names & {"retry_payment", "create_payment_link", "send_message"})
    assert all(t["moves_money"] is False for t in tools)
