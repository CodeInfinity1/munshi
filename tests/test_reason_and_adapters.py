"""The model is treated as an untrusted input, and adapters must not fake work."""

import json

import pytest

from munshi.adapters.base import UnsupportedInTestMode
from munshi.adapters.razorpay_test import normalise_downtime, verify_webhook
from munshi.adapters.simulator import SimulatorAdapter
from munshi.enrich import build_context
from munshi.models import ACTION_TIERS, ROOT_CAUSES
from munshi.reason import DECISION_SCHEMA, HeuristicReasoner, LLMReasoner
from munshi.seed.generate import BATCH_START
from tests.conftest import make_case


class _FakeLLM(LLMReasoner):
    """Bypasses the network so we can feed the validator arbitrary model output."""

    def __init__(self, payload):
        self._payload = payload
        self._model = "test-model"
        self._effort = "low"
        self._fallback = HeuristicReasoner()
        self.degraded = 0

    def _call(self, ctx):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


GOOD = {
    "root_cause": "payer_balance", "confidence": 0.8, "recoverability": 0.7,
    "diagnosis_rationale": "balance", "evidence": ["e"], "action_type": "retry_payment",
    "delay_hours": 12, "channel": "none", "message": "", "justification": "wait for salary",
}


def ctx(conn):
    return build_context(conn, make_case(conn), BATCH_START)


def test_schema_enums_are_generated_from_the_real_vocabularies():
    """The schema and the policy engine must agree on what an action is."""
    assert set(DECISION_SCHEMA["properties"]["action_type"]["enum"]) == set(ACTION_TIERS)
    assert set(DECISION_SCHEMA["properties"]["root_cause"]["enum"]) == set(ROOT_CAUSES)


def test_valid_model_output_is_accepted(conn):
    diag, plan = _FakeLLM(GOOD).decide(ctx(conn))
    assert plan.action_type == "retry_payment"
    assert diag.reasoner == "llm" and plan.delay_hours == 12


def test_invented_action_is_rejected_and_degrades(conn):
    """A model that returns an action outside the vocabulary must not be obeyed,
    and must not crash the batch either."""
    r = _FakeLLM({**GOOD, "action_type": "wire_transfer_to_agent_wallet"})
    diag, plan = r.decide(ctx(conn))
    assert plan.action_type in ACTION_TIERS
    assert diag.reasoner == "heuristic" and r.degraded == 1


def test_invented_root_cause_is_rejected(conn):
    r = _FakeLLM({**GOOD, "root_cause": "customer_is_a_fraudster"})
    diag, _ = r.decide(ctx(conn))
    assert diag.root_cause in ROOT_CAUSES and r.degraded == 1


def test_out_of_range_values_are_clamped_not_trusted(conn):
    diag, plan = _FakeLLM({**GOOD, "confidence": 42, "recoverability": -3,
                           "delay_hours": 99999}).decide(ctx(conn))
    assert 0 <= diag.confidence <= 1
    assert 0 <= diag.recoverability <= 1
    assert 0 <= plan.delay_hours <= 336


def test_model_failure_degrades_to_the_deterministic_reasoner(conn):
    r = _FakeLLM(RuntimeError("503 from the API"))
    diag, plan = r.decide(ctx(conn))
    assert diag.reasoner == "heuristic"
    assert "LLM unavailable" in diag.rationale
    assert plan.action_type in ACTION_TIERS
    assert r.degraded == 1


def test_unknown_channel_falls_back_to_none(conn):
    _, plan = _FakeLLM({**GOOD, "channel": "carrier_pigeon"}).decide(ctx(conn))
    assert plan.channel == "none"


# --------------------------------------------------------------------------
# Adapters
# --------------------------------------------------------------------------
def test_webhook_signature_must_match_the_raw_body():
    import hashlib
    import hmac

    secret, body = "whsec_x", b'{"event":"payment.failed"}'
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook(body, sig, secret)
    assert not verify_webhook(body + b"\n", sig, secret)   # re-serialised payload
    assert not verify_webhook(body, "0" * 64, secret)
    assert not verify_webhook(body, sig, None)             # no secret configured
    assert not verify_webhook(body, "", secret)


def test_downtime_entity_maps_to_storage_shape():
    out = normalise_downtime({"id": "down_1", "method": "upi", "entity": "payment.downtime",
                              "instrument": {"vpa_handle": "ybl"}, "begin": 100, "end": None,
                              "status": "started", "severity": "high", "scheduled": False})
    assert out["begin_at"] == 100 and out["end_at"] is None and out["scheduled"] == 0


def test_live_adapter_refuses_non_test_keys(monkeypatch):
    """A production key must never reach this adapter, even if configured."""
    from munshi.adapters.razorpay_test import RazorpayTestAdapter
    from munshi.config import Settings, settings

    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_abcdef")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "s")
    settings.cache_clear()
    try:
        assert Settings().razorpay_key_id == "rzp_live_abcdef"
        with pytest.raises(RuntimeError, match="TEST mode"):
            RazorpayTestAdapter()
    finally:
        settings.cache_clear()


def test_simulator_will_not_invent_an_outcome_without_ground_truth(conn):
    """A case with no latent record cannot be resolved, and the simulator says so
    rather than guessing."""
    case = make_case(conn, latent="{}")
    r = SimulatorAdapter().execute("retry_payment", case, {}, BATCH_START)
    assert r.outcome == "failed"
    assert r.recovered_paise == 0
    assert "no ground truth" in r.detail["precondition"]


def test_unsupported_action_is_raised_not_faked():
    assert issubclass(UnsupportedInTestMode, RuntimeError)


def test_decision_schema_meets_the_api_contract():
    """A json_schema output format requires additionalProperties:false and every
    property listed in `required`. Getting this wrong is a 400 at runtime, in an
    arm that only runs when someone supplies a key."""
    props = set(DECISION_SCHEMA["properties"])
    assert props == set(DECISION_SCHEMA["required"]), props ^ set(DECISION_SCHEMA["required"])
    assert DECISION_SCHEMA["additionalProperties"] is False
    assert DECISION_SCHEMA["type"] == "object"
    json.dumps(DECISION_SCHEMA)


def test_llm_request_is_well_formed_without_sending_it(monkeypatch):
    """Capture the request the reasoner would send. This is as far as it is
    possible to verify the live path without a credential -- stated as such in
    the README rather than implied to be tested end to end."""
    from munshi.config import Settings, settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    settings.cache_clear()
    try:
        assert Settings().llm_available
        r = LLMReasoner()
        captured = {}

        class _Messages:
            def create(self, **kw):
                captured.update(kw)
                raise RuntimeError("not sent")

        r._client = type("C", (), {"messages": _Messages()})()
        r.decide(ctx(_conn_for_request_test()))
    finally:
        settings.cache_clear()

    assert captured["model"]
    assert captured["output_config"]["format"]["type"] == "json_schema"
    assert captured["output_config"]["format"]["schema"] is DECISION_SCHEMA
    assert captured["output_config"]["effort"] in ("low", "medium", "high", "xhigh", "max")
    assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert captured["messages"][0]["role"] == "user"
    json.loads(captured["messages"][0]["content"])  # the context pack serialises


def _conn_for_request_test():
    from munshi import db

    c = db.reset("/tmp/munshi_req_test.db")
    return c
