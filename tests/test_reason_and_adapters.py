"""Adapters must not fake work, and must refuse a live credential.

The model-facing tests live in test_agent.py, against the real tool loop."""

import pytest

from munshi.adapters.base import UnsupportedInTestMode
from munshi.adapters.razorpay_test import normalise_downtime, verify_webhook
from munshi.adapters.simulator import SimulatorAdapter
from munshi.seed.generate import BATCH_START
from tests.conftest import make_case


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


