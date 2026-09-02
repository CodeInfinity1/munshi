"""The policy engine: deterministic, total, and outside the model's reach.

Every proposed action is checked against every rule below. There is no path by
which a persuasive rationale, an unusual failure code, or a malformed model
response widens what the agent may do -- the reasoning layer produces a proposal,
and this module decides what happens to it.

Three outcomes:
  allow             -- execute autonomously
  require_approval  -- queue for a human; nothing happens until someone signs off
  deny              -- refuse, with a reason, and either reschedule or stop

The distinction that matters most is **deny-for-now versus deny-forever**. A
contact attempted at 22:14 is not a dead case; it is a case that must wait until
08:00. A fourth retry is a dead case. Rules that refuse temporarily set
`reschedule_at`; rules that refuse permanently set `stop_reason`.

Every rule that runs is recorded -- passes included -- so the audit trail shows
what was checked, not only what failed.
"""

from __future__ import annotations

import sqlite3

from . import db, downtime
from .compliance import (
    contact_window_ok,
    mandate_debit_ok,
    next_contact_time,
    next_debit_window,
    npci_debit_window_ok,
)
from .config import settings
from .models import (
    ACTION_TIERS,
    CUSTOMER_CONTACTING,
    MONEY_MOVING,
    CaseState,
    PolicyDecision,
    RuleVerdict,
    Tier,
)
from .taxonomy import lookup

#: Merchant-configurable bounds. Surfaced verbatim in the UI so a merchant can see
#: exactly what the agent is and is not allowed to do.
POLICY = {
    "max_recovery_attempts": 3,
    "max_customer_contacts": 3,
    "min_hours_between_retries": 6,
    "min_hours_between_contacts": 20,
    "recovery_window_days": 14,
    # MERCHANT POLICY (not regulation): the largest single re-presentment the agent
    # may make without a human. Re-presenting an amount the customer already
    # authorised is low-risk, so this sits well above the regulatory figure below.
    "max_autonomous_retry_paise": 50_000 * 100,
    # REGULATION: the RBI AFA-free e-mandate ceiling. Above it, only the customer
    # can complete fresh authentication, so the agent cannot present the debit at all.
    "max_autonomous_action_paise": 15_000 * 100,
    # Total money-moving exposure one run may put in flight without sign-off.
    "max_run_exposure_paise": 25_00_000 * 100,
    "escalate_to_collections_min_paise": 25_000 * 100,
    "escalate_to_collections_min_days_overdue": 45,
}

RETRY_ACTIONS = frozenset({"retry_payment", "schedule_retry"})


class PolicyEngine:
    def __init__(self, conn: sqlite3.Connection, policy: dict | None = None):
        self.conn = conn
        self.p = {**POLICY, **(policy or {})}
        self.tz = settings().timezone
        self._run_exposure_paise = 0

    # -- exposure is per-run state, so it is reset explicitly by the orchestrator
    def begin_run(self) -> None:
        self._run_exposure_paise = 0

    @property
    def run_exposure_paise(self) -> int:
        return self._run_exposure_paise

    def evaluate(self, case: dict, plan, ctx: dict, now: int) -> PolicyDecision:
        rules: list[RuleVerdict] = []
        action = plan.action_type
        tier = ACTION_TIERS.get(action, Tier.FORBIDDEN)
        amount = int(case["amount_paise"])
        sem = lookup(case.get("error_reason"))
        is_contact = action in CUSTOMER_CONTACTING
        is_money = action in MONEY_MOVING
        is_retry = action in RETRY_ACTIONS

        def add(rule, passed, detail, escalate=None):
            rules.append(RuleVerdict(rule, passed, detail, escalate))

        # --- vocabulary and autonomy tier ------------------------------------
        add("action_in_vocabulary", action in ACTION_TIERS,
            f"{action} is a known action" if action in ACTION_TIERS
            else f"{action} is not in the action vocabulary", "deny")
        add("autonomy_tier", tier != Tier.FORBIDDEN,
            f"{action} is tier L{tier}"
            + (" (never executable by the agent)" if tier == Tier.FORBIDDEN else ""), "deny")
        if tier in (Tier.RECOMMEND, Tier.APPROVAL):
            add("tier_requires_human", False,
                f"{action} is tier L{tier}; it is surfaced to the merchant and never "
                "executed autonomously", "require_approval")

        # --- terminal states --------------------------------------------------
        add("case_not_terminal", case["state"] not in CaseState.TERMINAL,
            f"case state is {case['state']}", "deny")

        # --- money already collected -----------------------------------------
        already_paid = sem.family == "already_settled"
        if already_paid and action != "suppress_case":
            add("not_already_settled", False,
                "Razorpay reports order_already_paid: this money is already collected, and "
                "chasing it would contact a customer who has already paid", "deny")
            return self._finish(rules, stop_reason="already_settled")
        add("not_already_settled", True,
            "suppressing an already-settled case is the correct action" if already_paid
            else "no successful payment recorded for this entity")

        # --- risk holds -------------------------------------------------------
        if sem.family == "risk_flagged":
            offending = is_retry or is_contact or is_money
            add("risk_decline_hold", not offending,
                "risk-declined payments are held for human review; an automated system "
                "must not retry or chase its way past a risk decision"
                if offending else "action does not attempt to bypass the risk decline",
                "deny")
            if offending:
                return self._finish(rules, stop_reason="risk_decline_requires_human_review")

        # --- retryability from the taxonomy ----------------------------------
        if is_retry:
            futile = sem.retry_is_futile
            add("retry_can_succeed", not futile,
                f"{sem.reason}: {sem.resolution_requires}" if futile
                else f"{sem.reason} is retryable once {sem.resolution_requires}", "deny")

            attempts = case["attempts"]
            cap = self.p["max_recovery_attempts"]
            add("retry_budget", attempts < cap, f"{attempts}/{cap} recovery attempts used", "deny")
            if attempts >= cap:
                return self._finish(rules, stop_reason="max_retry_attempts_reached")

            last = self._last_execution(case["id"], RETRY_ACTIONS)
            cooldown_h = max(self.p["min_hours_between_retries"], sem.min_backoff_hours or 0)
            if last is not None:
                elapsed = (now - last) / 3600
                ok = elapsed >= cooldown_h
                add("retry_cooldown", ok,
                    f"{elapsed:.1f}h since last attempt, floor is {cooldown_h:.0f}h "
                    f"({sem.reason} needs {sem.min_backoff_hours}h to have any chance)")
                if not ok:
                    return self._finish(rules, reschedule_at=int(last + cooldown_h * 3600))
            else:
                add("retry_cooldown", True, "no prior recovery attempt on this case")

            dt = ctx["downtime"]
            holds = dt.get("consecutive_holds", 0)
            blocked = (
                dt.get("state") == "active"
                and dt.get("severity") in ("high", "medium")
                # Waiting is only rational while waiting is bounded. Past this, the
                # outage is no longer a reason to hold the case hostage.
                and holds < downtime.MAX_CONSECUTIVE_HOLDS
            )
            add("downtime_clear", not blocked,
                dt.get("note", "Razorpay reports no downtime for this instrument")
                if not blocked else
                f"Razorpay reports an active {dt['severity']}-severity downtime on this "
                f"instrument (hold {holds + 1}/{downtime.MAX_CONSECUTIVE_HOLDS}); a retry "
                f"now would spend one of {cap} attempts for near-zero expected value")
            if blocked:
                return self._finish(rules,
                                    reschedule_at=int(now + dt.get("hold_hours", 2) * 3600))

        # --- recurring debits: RBI e-mandate framework ------------------------
        if is_retry and case.get("method") == "emandate":
            open_now, note = npci_debit_window_ok(now, self.tz)
            add("npci_debit_window", open_now, note)
            if not open_now:
                return self._finish(rules, reschedule_at=next_debit_window(now, self.tz))

            notified = self._last_execution(case["id"], {"send_reminder"})
            ok, note = mandate_debit_ok(amount, notified, now)
            # Above the AFA ceiling this is permanent -- only the customer can
            # re-authenticate. Below it, the notice merely has not been sent yet,
            # which is a deferral, not a write-off.
            permanent = amount > self.p["max_autonomous_action_paise"]
            add("emandate_pre_debit_notice", ok, note, "deny" if permanent else None)
            if not ok:
                # Above the AFA ceiling only the customer can re-authenticate; below
                # it we simply have not sent the notification yet, so send it.
                if amount > self.p["max_autonomous_action_paise"]:
                    # Only the customer can complete fresh AFA. Not a dead case -- a
                    # case whose next action is a re-authorisation link.
                    return self._finish(rules, stop_reason="emandate_requires_customer_afa")
                # The notice simply has not been sent yet. Come back once it could
                # have been, and let the reasoner propose sending it.
                return self._finish(rules, reschedule_at=int(now + 3600))

        # --- customer contact -------------------------------------------------
        if is_contact:
            opted_out = bool(ctx["customer"].get("contact_opt_out"))
            add("customer_contactable", not opted_out,
                "customer has opted out of recovery communication" if opted_out
                else "customer has not opted out", "deny")
            if opted_out:
                return self._finish(rules, stop_reason="customer_opted_out")

            wrong_target = not sem.contacts_customer
            add("contact_targets_the_right_party", not wrong_target,
                f"{sem.reason} must be fixed by {sem.blame}; contacting the customer "
                "cannot resolve it" if wrong_target else
                f"{sem.blame} must act, and customer contact is the way to reach them",
                "deny")
            if wrong_target:
                return self._finish(rules, stop_reason="not_a_customer_resolvable_failure")

            sent, cap = case["contacts_sent"], self.p["max_customer_contacts"]
            add("contact_budget", sent < cap, f"{sent}/{cap} customer contacts used", "deny")
            if sent >= cap:
                return self._finish(rules, stop_reason="max_contacts_reached")

            promise = case.get("promise_to_pay_at")
            if promise and now < promise:
                add("promise_to_pay_hold", False,
                    f"customer committed to pay by {promise}; chasing before that date "
                    "breaks a commitment we accepted")
                return self._finish(rules, reschedule_at=int(promise))
            add("promise_to_pay_hold", True, "no active promise to pay on this case")

            last_contact = self._last_execution(case["id"], CUSTOMER_CONTACTING)
            if last_contact is not None:
                elapsed = (now - last_contact) / 3600
                floor = self.p["min_hours_between_contacts"]
                ok = elapsed >= floor
                add("contact_cooldown", ok,
                    f"{elapsed:.1f}h since last contact, floor is {floor}h")
                if not ok:
                    return self._finish(rules,
                                        reschedule_at=int(last_contact + floor * 3600))
            else:
                add("contact_cooldown", True, "no prior contact on this case")

            allowed, note = contact_window_ok(now, self.tz)
            add("rbi_contact_window", allowed, note)
            if not allowed:
                return self._finish(rules, reschedule_at=next_contact_time(now, self.tz))

        # --- financial exposure ----------------------------------------------
        if is_money:
            cap = (self.p["max_autonomous_retry_paise"] if is_retry
                   else self.p["max_autonomous_action_paise"])
            source = "merchant policy" if is_retry else "RBI AFA-free e-mandate ceiling"
            within = amount <= cap
            add("autonomous_amount_ceiling", within,
                f"Rs {amount / 100:,.0f} vs autonomous ceiling Rs {cap / 100:,.0f} ({source})",
                None if within else "require_approval")

            projected = self._run_exposure_paise + amount
            run_cap = self.p["max_run_exposure_paise"]
            add("run_exposure_cap", projected <= run_cap,
                f"run exposure would reach Rs {projected / 100:,.0f} of Rs {run_cap / 100:,.0f}",
                "deny")
            if projected > run_cap:
                return self._finish(rules, stop_reason="run_exposure_cap_reached")

        # --- collections escalation is not a first resort ---------------------
        if action == "escalate_to_collections":
            eligible = (
                amount >= self.p["escalate_to_collections_min_paise"]
                and case["days_overdue"] >= self.p["escalate_to_collections_min_days_overdue"]
            )
            add("collections_threshold", eligible,
                f"Rs {amount / 100:,.0f} at {case['days_overdue']}d overdue vs threshold "
                f"Rs {self.p['escalate_to_collections_min_paise'] / 100:,.0f} at "
                f"{self.p['escalate_to_collections_min_days_overdue']}d", "deny")

        # --- the outer bound on the whole case --------------------------------
        age_days = (now - case["opened_at"]) / 86400
        window = self.p["recovery_window_days"]
        in_window = age_days <= window
        add("recovery_window", in_window,
            f"case is {age_days:.1f}d old, window is {window}d", "deny")
        if not in_window:
            return self._finish(rules, stop_reason="recovery_window_expired")

        # --- idempotency -------------------------------------------------------
        dup = db.one(
            self.conn,
            "SELECT id FROM actions WHERE case_id=? AND action_type=? AND executed_at IS NOT NULL"
            " AND outcome='success'",
            (case["id"], action),
        )
        add("no_duplicate_successful_action", dup is None,
            f"{action} already executed successfully on this case" if dup
            else f"no successful {action} on this case yet", "deny")

        decision = self._finish(rules)
        if decision.decision == "allow" and is_money:
            self._run_exposure_paise += amount
        return decision

    # ------------------------------------------------------------------
    def _finish(self, rules, stop_reason=None, reschedule_at=None) -> PolicyDecision:
        """Resolve the verdicts. deny beats require_approval beats allow, and a
        permanent denial beats a temporary one."""
        hard_deny = next((r for r in rules if not r.passed and r.escalate_to == "deny"), None)
        if hard_deny is not None and reschedule_at is not None:
            # A later rule asked to defer, but an earlier rule already said this can
            # never work. Deferring would loop the case until the window expires.
            reschedule_at = None
            stop_reason = stop_reason or hard_deny.rule
        decision = "allow"
        for r in rules:
            if r.passed:
                continue
            if r.escalate_to == "deny":
                decision = "deny"
                break
            if r.escalate_to == "require_approval":
                decision = "require_approval"
            # A failing rule with no declared escalation is informational: it is
            # recorded in the audit trail but cannot by itself deny. Temporary
            # refusals come only from an explicit reschedule_at, so a rule can
            # never accidentally terminate a live case.
        if stop_reason or reschedule_at:
            decision = "deny"
        return PolicyDecision(decision=decision, rules=rules, stop_reason=stop_reason,
                              reschedule_at=reschedule_at)

    def _last_execution(self, case_id: str, action_types) -> int | None:
        placeholders = ",".join("?" * len(action_types))
        row = db.one(
            self.conn,
            f"SELECT MAX(executed_at) AS t FROM actions WHERE case_id=? AND action_type IN"
            f" ({placeholders}) AND executed_at IS NOT NULL",
            (case_id, *action_types),
        )
        return row["t"] if row and row["t"] else None
