"""Razorpay adapter. Issues real calls against Razorpay TEST mode.

Selected only when MUNSHI_ADAPTER=razorpay_test *and* test-mode credentials are
present, so it can never be reached by accident.

What it genuinely performs in test mode:
  send_recovery_link / send_instrument_update_link / send_mandate_reauth_link /
  send_reminder   -> POST /v1/payment_links  (+ notify_by for a resend)
  verification    -> GET  /v1/payments/:id
  downtime feed   -> GET  /v1/payments/downtimes

What it refuses to fake:
  retry_payment on a recurring mandate is `POST /v1/payments/:id/recurring` in
  production and needs a live customer-authorised mandate token, which test mode
  cannot mint. Rather than simulate the call and report it as if Razorpay had run
  it, this adapter raises UnsupportedInTestMode and the executor records the
  action as not_executed with that reason. The one thing a revenue-recovery demo
  must never do is claim a payment rail ran when it did not.

`reference_id` is Razorpay-enforced unique per link, so it doubles as the
idempotency key: a redelivered webhook cannot mint a second link for the same
recovery attempt.
"""

from __future__ import annotations

import hashlib
import hmac
import logging

import httpx

from ..config import settings
from ..db import jload
from .base import ActionResult, UnsupportedInTestMode

log = logging.getLogger("munshi.razorpay")
API = "https://api.razorpay.com/v1"

LINK_ACTIONS = {
    "send_recovery_link": "Complete your payment",
    "send_instrument_update_link": "Update your payment method",
    "send_mandate_reauth_link": "Re-authorise your auto-pay mandate",
    "send_reminder": "Payment reminder",
}


class RazorpayTestAdapter:
    name = "razorpay_test"

    def __init__(self, client: httpx.Client | None = None):
        s = settings()
        if not s.razorpay_credentials_present:
            raise RuntimeError("RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are not set")
        if not s.razorpay_key_id.startswith("rzp_test_"):
            raise RuntimeError(
                f"refusing to run against key id {s.razorpay_key_id[:12]}...: Munshi only "
                "executes against Razorpay TEST mode keys (rzp_test_*)"
            )
        self._client = client or httpx.Client(
            base_url=API, auth=(s.razorpay_key_id, s.razorpay_key_secret), timeout=20.0
        )

    # ------------------------------------------------------------------
    def execute(self, action_type: str, case: dict, params: dict, now: int) -> ActionResult:
        if action_type in LINK_ACTIONS:
            return self._payment_link(action_type, case, params)
        if action_type == "retry_payment":
            raise UnsupportedInTestMode(
                "re-presenting a failed charge requires a customer-authorised mandate "
                "token (POST /v1/payments/:id/recurring); Razorpay test mode cannot mint "
                "one, so this action is recorded as not executed rather than simulated"
            )
        if action_type in ("offer_partial_payment", "issue_discount"):
            raise UnsupportedInTestMode(
                f"{action_type} changes commercial terms and is gated behind merchant "
                "approval; it is not executed by the adapter"
            )
        return ActionResult(outcome="success", detail={"kind": "operational"}, simulated=False)

    def _payment_link(self, action_type: str, case: dict, params: dict) -> ActionResult:
        body = {
            "amount": int(case["amount_paise"]),
            "currency": case.get("currency", "INR"),
            "accept_partial": action_type == "offer_partial_payment",
            "description": LINK_ACTIONS[action_type][:255],
            # Razorpay enforces uniqueness on reference_id, which makes it a free
            # idempotency key across webhook redeliveries.
            "reference_id": params["idempotency_key"],
            "customer": {
                "name": params.get("customer_name", "")[:100],
                "email": params.get("customer_email") or None,
                "contact": params.get("customer_phone") or None,
            },
            "notify": {"sms": params.get("channel") == "sms",
                       "email": params.get("channel") == "email"},
            "reminder_enable": False,  # Munshi owns the reminder cadence, not Razorpay
            "notes": {"munshi_case_id": case["id"], "munshi_action": action_type},
        }
        try:
            r = self._client.post("/payment_links", json=body)
            if r.status_code == 400 and "reference_id" in r.text:
                # The link already exists: a redelivery, not a new attempt.
                return ActionResult(outcome="success", simulated=False,
                                    detail={"idempotent_replay": True, "provider": "razorpay"})
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPError as exc:
            log.warning("razorpay payment_link failed for %s: %s", case["id"], exc)
            return ActionResult(outcome="failed", simulated=False,
                                detail={"error": str(exc)[:300], "provider": "razorpay"})
        return ActionResult(
            outcome="pending",  # money only counts once the payment is actually captured
            provider_ref=data.get("id"),
            simulated=False,
            detail={"short_url": data.get("short_url"), "status": data.get("status"),
                    "provider": "razorpay", "mode": "test"},
        )

    # ------------------------------------------------------------------
    def fetch_payment(self, payment_id: str) -> dict:
        r = self._client.get(f"/payments/{payment_id}")
        r.raise_for_status()
        return r.json()

    def fetch_downtimes(self) -> list[dict]:
        """GET /v1/payments/downtimes -- the live instrument outage feed."""
        r = self._client.get("/payments/downtimes")
        r.raise_for_status()
        return r.json().get("items", [])


def verify_webhook(body: bytes, signature: str, secret: str | None = None) -> bool:
    """Validate X-Razorpay-Signature: HMAC-SHA256 of the raw request body.

    Must run against the raw bytes, before any JSON parsing -- re-serialising the
    payload changes the bytes and the signature will never match. Compared in
    constant time.
    """
    secret = secret or settings().razorpay_webhook_secret
    if not secret:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def normalise_downtime(item: dict) -> dict:
    """Razorpay downtime entity -> Munshi's downtimes row shape."""
    return {
        "id": item["id"], "method": item["method"],
        "instrument": item.get("instrument", {}),
        "begin_at": item.get("begin"), "end_at": item.get("end"),
        "status": item.get("status", "started"), "severity": item.get("severity", "low"),
        "scheduled": int(bool(item.get("scheduled"))),
    }


def build_adapter():
    """Adapter selection. Simulator unless razorpay_test is explicitly configured."""
    if settings().effective_adapter == "razorpay_test":
        return RazorpayTestAdapter()
    from .simulator import SimulatorAdapter

    return SimulatorAdapter()


_ = jload  # re-exported for the webhook path
