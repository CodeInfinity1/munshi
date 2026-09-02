"""The baseline: a fixed retry ladder, which is what most merchants actually run.

Retry every failed payment at +6h, +24h and +72h; send a generic reminder after
each failure; cap at 3 retries and 3 contacts. No failure-reason awareness, no
downtime correlation, no compliance windows, no suppression of customers who
have already paid.

Two deliberate fairness choices:

- The baseline keeps the retry and contact **caps**, because almost every real
  dunning setup has them. The comparison is meant to isolate *judgement*, not to
  win by giving the baseline no limits at all.
- It runs through the **same executor and the same outcome oracle with the same
  per-case seeds**. Identical cases, identical luck. Only the choice of action
  and its timing differ.

What it does not get is the compliance envelope -- because a naive dunning cron
genuinely does fire at 02:00. Those attempts are executed and *counted*, which is
how the harness can report the violations Munshi's policy engine prevented.
"""

from __future__ import annotations

from ..compliance import contact_window_ok, npci_debit_window_ok
from ..config import settings
from ..models import ACTION_TIERS, PolicyDecision, RuleVerdict, Tier

#: Hours after the original failure at which each successive retry fires.
LADDER_HOURS = (6, 24, 72)


class FixedLadderReasoner:
    """Reason-blind dunning. Every failure gets the same treatment."""

    name = "fixed_ladder"

    def decide(self, ctx: dict):
        from ..models import Diagnosis, Plan

        case = ctx["case"]
        attempts = case["munshi_attempts"]
        diag = Diagnosis(
            root_cause="unknown", confidence=0.0, recoverability=0.5,
            rationale="Fixed ladder does not diagnose; every failure is treated alike.",
            evidence=[], reasoner=self.name,
        )
        if case["kind"] in ("invoice_overdue", "checkout_abandoned") or attempts >= len(
            LADDER_HOURS
        ):
            return diag, Plan(
                action_type="send_reminder", params={}, delay_hours=0.0,
                channel="email",
                message=f"A payment of Rs {case['amount_inr']:,.0f} is pending. "
                        "Please complete it at your earliest convenience.",
                justification="Ladder exhausted or non-retryable entity: send a reminder.",
                reasoner=self.name,
            )
        # Fire the next rung, measured from the original failure.
        target = LADDER_HOURS[attempts]
        delay = max(0.0, target - case["age_hours"])
        return diag, Plan(
            action_type="retry_payment", params={}, delay_hours=delay, channel="none",
            message=None, reasoner=self.name,
            justification=f"Fixed ladder rung {attempts + 1}/{len(LADDER_HOURS)} at "
                          f"+{target}h after the original failure.",
        )


class LadderPolicy:
    """Caps only. Records compliance breaches instead of preventing them.

    Same interface as `PolicyEngine`, so the orchestrator cannot tell them apart.
    """

    def __init__(self, conn, policy: dict | None = None):
        self.conn = conn
        # Same key set as PolicyEngine so the orchestrator cannot tell them apart.
        # A fixed ladder re-contacts on each rung, so its cooldowns are short.
        self.p = {"max_recovery_attempts": 3, "max_customer_contacts": 3,
                  "recovery_window_days": 14, "min_hours_between_retries": 6,
                  "min_hours_between_contacts": 6, **(policy or {})}
        self.tz = settings().timezone
        self.violations: list[dict] = []

    def begin_run(self) -> None:
        self.violations.clear()

    @property
    def run_exposure_paise(self) -> int:
        return 0

    def evaluate(self, case, plan, ctx, now) -> PolicyDecision:
        rules: list[RuleVerdict] = []
        action = plan.action_type
        tier = ACTION_TIERS.get(action, Tier.FORBIDDEN)

        if tier == Tier.FORBIDDEN:
            rules.append(RuleVerdict("autonomy_tier", False, f"{action} is L4", "deny"))
            return PolicyDecision("deny", rules, stop_reason="forbidden_action")

        if action == "retry_payment":
            ok = case["attempts"] < self.p["max_recovery_attempts"]
            rules.append(RuleVerdict("retry_budget", ok,
                                     f"{case['attempts']}/{self.p['max_recovery_attempts']}",
                                     None if ok else "deny"))
            if not ok:
                return PolicyDecision("deny", rules, stop_reason="max_retry_attempts_reached")
            if case.get("method") == "emandate":
                open_now, note = npci_debit_window_ok(now, self.tz)
                if not open_now:
                    self._violate("npci_debit_window", case, action, now, note)

        if action == "send_reminder":
            ok = case["contacts_sent"] < self.p["max_customer_contacts"]
            rules.append(RuleVerdict("contact_budget", ok,
                                     f"{case['contacts_sent']}/{self.p['max_customer_contacts']}",
                                     None if ok else "deny"))
            if not ok:
                return PolicyDecision("deny", rules, stop_reason="max_contacts_reached")
            allowed, note = contact_window_ok(now, self.tz)
            if not allowed:
                self._violate("rbi_contact_window", case, action, now, note)
            if ctx["customer"].get("contact_opt_out"):
                self._violate("customer_opt_out", case, action, now,
                              "message sent to a customer who opted out of recovery contact")

        age_days = (now - case["opened_at"]) / 86400
        in_window = age_days <= self.p["recovery_window_days"]
        rules.append(RuleVerdict("recovery_window", in_window, f"{age_days:.1f}d",
                                 None if in_window else "deny"))
        if not in_window:
            return PolicyDecision("deny", rules, stop_reason="recovery_window_expired")
        return PolicyDecision("allow", rules)

    def _violate(self, rule: str, case, action: str, now: int, detail: str) -> None:
        self.violations.append({"rule": rule, "case_id": case["id"], "action": action,
                                "ts": now, "detail": detail})
