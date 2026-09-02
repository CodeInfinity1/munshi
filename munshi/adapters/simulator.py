"""Deterministic outcome oracle.

This decides whether an action actually moved money. Three properties make the
resulting numbers worth reporting:

1. **It resolves against latent ground truth, not against the agent's opinion.**
   The oracle is handed the case's hidden state -- when the payer's balance really
   tops up, whether the customer would really replace a dead card, when the outage
   really clears -- and the action's timing. It never sees the diagnosis, the
   rationale, or which arm produced the action. An agent cannot talk its way into
   a recovery.

2. **Luck is fixed per (case, action, attempt).** The coin flip is seeded from the
   case's own seed, so the agent arm and the baseline arm draw *the same* luck on
   the same case. What differs between arms is which action was chosen and when --
   which is exactly the counterfactual we want to measure.

3. **The conditional probabilities are stated, not buried.** The table below is
   the model's entire content, and it is derived from the documented resolution
   condition for each taxonomy family (Razorpay's own "next steps" guidance),
   not tuned to make the agent look good. Notably it is generous to retries when
   the precondition is met -- which flatters the fixed-ladder baseline, not us.

Every rupee this produces is SIMULATED and is labelled as such in the ledger, in
the API and in the UI.
"""

from __future__ import annotations

import hashlib
import math
import random

from ..db import jload
from ..taxonomy import lookup
from .base import ActionResult

#: Success probability of a retry once the family's resolution condition is met,
#: and while it is not.
RETRY_ODDS = {
    #                       precondition met, precondition not met
    "balance_dependent":   (0.86, 0.06),   # money is there / money is not there
    "transient_infra":     (0.88, 0.11),   # outage cleared / still degraded
    "limit_bound":         (0.82, 0.04),   # limit window reset / still inside it
    "merchant_config":     (0.74, 0.00),   # merchant fixed config / has not
    "customer_dropout":    (0.05, 0.05),   # a server-side retry cannot supply an OTP
    "instrument_dead":     (0.00, 0.00),   # the instrument cannot authorise anything
    "mandate_broken":      (0.00, 0.00),   # the debit authority itself is gone
    "risk_flagged":        (0.00, 0.00),
    "integration_bug":     (0.00, 0.00),   # the request is malformed; so is the retry
    "already_settled":     (0.00, 0.00),
}

#: Base conversion of a customer-facing action, before intent, responsiveness and
#: time decay are applied.
CONTACT_BASE = {
    "send_recovery_link": 0.42,
    "send_instrument_update_link": 0.34,
    "send_mandate_reauth_link": 0.30,
    "send_reminder": 0.26,
    "offer_partial_payment": 0.55,
    "issue_discount": 0.58,
    "escalate_to_collections": 0.38,
}

INTENT_MULTIPLIER = {"warm": 1.35, "lukewarm": 1.0, "cold": 0.55}

#: Half-life of customer intent, in hours. Somebody who abandoned an OTP screen
#: ten minutes ago is a different prospect from the same person next Tuesday.
INTENT_HALF_LIFE_H = {
    "customer_dropout": 14.0,
    "instrument_dead": 96.0,
    "mandate_broken": 120.0,
    "balance_dependent": 72.0,
}
DEFAULT_HALF_LIFE_H = 72.0

#: Fraction of the invoice a partial-payment offer actually collects.
PARTIAL_PAYMENT_FRACTION = 0.6

OPERATIONAL = {
    "no_action", "suppress_case", "escalate_to_merchant_ops",
    "open_engineering_ticket", "write_off",
}


def _rng(seed: int, action: str, attempt: int) -> random.Random:
    """Same case + same action + same attempt index => same draw, in every arm."""
    h = hashlib.sha256(f"{seed}:{action}:{attempt}".encode()).hexdigest()[:16]
    return random.Random(int(h, 16))


class SimulatorAdapter:
    name = "simulator"

    def execute(self, action_type: str, case: dict, params: dict, now: int) -> ActionResult:
        latent = jload(case.get("latent"), {}) or {}
        if action_type in OPERATIONAL:
            return ActionResult(outcome="success", detail={"kind": "operational"})
        if action_type == "retry_payment":
            return self._retry(case, latent, now)
        if action_type in CONTACT_BASE:
            return self._contact(action_type, case, latent, now)
        return ActionResult(outcome="not_executed",
                            detail={"reason": f"simulator has no model for {action_type}"})

    # ------------------------------------------------------------------
    def _retry(self, case: dict, latent: dict, now: int) -> ActionResult:
        family = latent.get("family") or lookup(case.get("error_reason")).family
        attempt = int(case["attempts"])
        elapsed_h = (now - case["opened_at"]) / 3600
        met, precondition = self._precondition_met(family, latent, elapsed_h)
        p_met, p_unmet = RETRY_ODDS.get(family, (0.0, 0.0))
        p = p_met if met else p_unmet
        if not latent.get("recoverable", False):
            p = 0.0

        draw = _rng(latent.get("seed", 0), "retry", attempt).random()
        detail = {
            "precondition": precondition, "precondition_met": met,
            "hours_since_failure": round(elapsed_h, 1), "p_success": p,
            "simulated": True,
        }
        if draw < p:
            return ActionResult(
                outcome="success", recovered_paise=int(case["amount_paise"]),
                provider_ref=f"pay_SIM{_ref(case['id'], attempt)}",
                detail={**detail, "captured": True},
            )
        return ActionResult(outcome="failed", detail={**detail, "captured": False})

    #: Which latent field carries each family's retry precondition, and what it means.
    _PRECONDITIONS = {
        "balance_dependent": ("funds_available_after_h", "payer balance covers the amount"),
        "transient_infra": ("outage_clears_after_h", "underlying outage clears"),
        "limit_bound": ("limit_resets_after_h", "instrument limit window resets"),
        "merchant_config": ("merchant_fixes_after_h", "merchant corrects the configuration"),
    }

    def _precondition_met(self, family: str, latent: dict, elapsed_h: float) -> tuple[bool, str]:
        """The single fact that determines whether a retry could work right now."""
        entry = self._PRECONDITIONS.get(family)
        if entry is None:
            return False, "no retry precondition can be satisfied for this failure family"
        field, description = entry
        h = latent.get(field)
        if h is None:
            # No ground truth for this case (e.g. one ingested live rather than
            # generated). The simulator will not guess an outcome it cannot resolve.
            return False, f"{description}: unknown, no ground truth for this case"
        return elapsed_h >= h, f"{description} at +{h}h"

    # ------------------------------------------------------------------
    def _contact(self, action_type: str, case: dict, latent: dict, now: int) -> ActionResult:
        family = latent.get("family", "")
        elapsed_h = max(0.0, (now - case["opened_at"]) / 3600)
        half_life = INTENT_HALF_LIFE_H.get(family, DEFAULT_HALF_LIFE_H)
        decay = math.exp(-elapsed_h / half_life * math.log(2))

        p = (
            CONTACT_BASE[action_type]
            * INTENT_MULTIPLIER.get(latent.get("intent", "lukewarm"), 1.0)
            * float(latent.get("responds_to_contact", 0.5))
            * decay
        )
        # An update link only converts if this customer would actually re-attach an
        # instrument; a re-auth link only if the mandate can be re-registered.
        if action_type == "send_instrument_update_link" and not latent.get(
            "will_replace_instrument", False
        ):
            p = 0.0
        if not latent.get("recoverable", False):
            p = 0.0

        attempt = int(case["contacts_sent"])
        rng = _rng(latent.get("seed", 0), "contact", attempt)
        detail = {"p_convert": round(p, 4), "intent": latent.get("intent"),
                  "decay_factor": round(decay, 3), "hours_since_failure": round(elapsed_h, 1),
                  "simulated": True}

        # A chased receivable often produces a commitment rather than a payment.
        # That is a real outcome and it is treated as one: it stops the chasing.
        if (
            case["kind"] == "invoice_overdue"
            and latent.get("will_promise_to_pay")
            and attempt == 0
            and rng.random() < 0.7
        ):
            promise_at = now + int(rng.choice([3, 5, 7, 10]) * 86400)
            return ActionResult(
                outcome="pending",
                detail={**detail, "customer_reply": "promise_to_pay", "promise_to_pay_at": promise_at,
                        "honours_promise": bool(latent.get("honours_promise"))},
            )

        if rng.random() < p:
            fraction = PARTIAL_PAYMENT_FRACTION if action_type == "offer_partial_payment" else 1.0
            return ActionResult(
                outcome="success", recovered_paise=int(case["amount_paise"] * fraction),
                provider_ref=f"pay_SIM{_ref(case['id'], 100 + attempt)}",
                detail={**detail, "converted": True, "fraction": fraction},
            )
        return ActionResult(outcome="failed", detail={**detail, "converted": False})


def _ref(case_id: str, n: int) -> str:
    return hashlib.sha256(f"{case_id}:{n}".encode()).hexdigest()[:12].upper()
