"""Assemble the context pack a decision is made from.

Everything here is deterministic retrieval and lookup -- no model involved. By
the time the reasoning layer runs, the hard facts (what the failure code means,
whether the instrument is in an outage, whether we are allowed to make contact
right now, what we have already tried) are already resolved. The model's job is
to weigh a small, closed set of facts, not to recall payments trivia.

The pack is also exactly what gets rendered in the UI next to the decision, so a
merchant sees the same evidence the agent saw.
"""

from __future__ import annotations

import sqlite3

from . import db, downtime
from .compliance import AFA_FREE_LIMIT_PAISE, contact_window_ok, npci_debit_window_ok
from .config import settings
from .db import jload
from .policy import POLICY
from .quantify import quantify
from .taxonomy import lookup


def build_context(conn: sqlite3.Connection, case: sqlite3.Row | dict, now: int) -> dict:
    case = dict(case)
    tz = settings().timezone
    sem = lookup(case.get("error_reason"))
    cust = db.one(conn, "SELECT * FROM customers WHERE id = ?", (case["customer_id"],))
    cust = dict(cust) if cust else {}

    prior = db.rows(
        conn,
        "SELECT action_type, policy_decision, outcome, executed_at, recovered_paise,"
        " proposed_at FROM actions WHERE case_id = ? ORDER BY proposed_at",
        (case["id"],),
    )
    contact_ok, contact_note = contact_window_ok(now, tz)
    pre_debit_at = next(
        (r["executed_at"] for r in prior
         if r["action_type"] == "send_reminder" and r["executed_at"]), None
    )
    debit_ok, debit_note = npci_debit_window_ok(now, tz)
    dt_status = downtime.status_for(conn, case, now)

    return {
        "case": {
            "id": case["id"],
            "kind": case["kind"],
            "entity_id": case["entity_id"],
            "amount_inr": round(case["amount_paise"] / 100, 2),
            "currency": case["currency"],
            "method": case["method"],
            "instrument": jload(case.get("instrument"), {}),
            "age_hours": round((now - case["opened_at"]) / 3600, 1),
            "days_overdue": case["days_overdue"],
            "state": case["state"],
            "munshi_attempts": case["attempts"],
            "attempts_before_munshi": case["prior_attempts"],
            "contacts_sent": case["contacts_sent"],
            # Bounded resources. An action whose budget is spent is not available,
            # and proposing it wastes the tick. These read the default policy: the
            # engine is authoritative and re-checks them, so a per-engine override
            # can only make the reasoner more conservative than necessary, never
            # less.
            "retries_remaining": max(0, POLICY["max_recovery_attempts"] - case["attempts"]),
            "contacts_remaining": max(0, POLICY["max_customer_contacts"] - case["contacts_sent"]),
        },
        "failure": {
            "error_source": case.get("error_source"),
            "error_step": case.get("error_step"),
            "error_reason": case.get("error_reason"),
            "family": sem.family,
            "family_label": sem.family_label,
            "retryability": sem.retryability,
            "retry_on_same_instrument_is_futile": sem.retry_is_futile,
            "who_must_act": sem.blame,
            "razorpay_description": sem.description,
            "razorpay_next_step": sem.next_step,
            "resolution_requires": sem.resolution_requires,
            "min_backoff_hours": sem.min_backoff_hours,
            "taxonomy_default_intervention": sem.default_intervention,
        },
        "downtime": {**dt_status, "consecutive_holds": case["downtime_holds"]},
        "customer": {
            "id": cust.get("id"),
            "segment": cust.get("segment"),
            "tenure_days": cust.get("tenure_days"),
            "lifetime_value_inr": round((cust.get("lifetime_paise") or 0) / 100, 2),
            "successful_payments": cust.get("successful_payments"),
            "failed_payments": cust.get("failed_payments"),
            "prior_recoveries": cust.get("prior_recoveries"),
            "contact_opt_out": bool(cust.get("contact_opt_out")),
            "preferred_channel": cust.get("preferred_channel"),
            "typical_success_hour_local": cust.get("typical_success_hour"),
            "is_established": (cust.get("successful_payments") or 0) >= 6,
        },
        "history": [
            {
                "action": r["action_type"],
                "policy": r["policy_decision"],
                "outcome": r["outcome"],
                "recovered_inr": round((r["recovered_paise"] or 0) / 100, 2),
                "hours_ago": round((now - r["proposed_at"]) / 3600, 1),
            }
            for r in prior
        ],
        "compliance": {
            "contact_allowed_now": contact_ok,
            "contact_note": contact_note,
            "npci_debit_window_open": debit_ok,
            "npci_note": debit_note,
            "pre_debit_notification_sent": pre_debit_at is not None,
            "pre_debit_notice_hours": (
                round((now - pre_debit_at) / 3600, 1) if pre_debit_at else None
            ),
            "mandate_amount_needs_customer_afa": (
                case["method"] == "emandate" and case["amount_paise"] > AFA_FREE_LIMIT_PAISE
            ),
            "timezone": tz,
        },
        "money": quantify(case),
    }
