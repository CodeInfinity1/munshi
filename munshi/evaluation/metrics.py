"""Metrics computed from a finished run.

Two rules the whole harness obeys:

- **A rupee counts as recovered only if it has a ledger row.** Every money figure
  here is a SUM over `ledger`, joined back to the action that caused it. There is
  no "estimated recovered" anywhere in this module.
- **Anything scored against ground truth reads `cases.latent`**, which no arm can
  see. That is what makes intervention accuracy and wasted-retry rate real
  measurements rather than the agent grading its own homework.
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict

from .. import db
from ..db import jload
from ..taxonomy import lookup

#: Families where a retry has a documented zero success probability -- the
#: instrument, the mandate or the request itself cannot authorise the amount.
#: A retry here is not a gamble, it is a certainty of failure.
ZERO_YIELD_RETRY_FAMILIES = frozenset({
    "instrument_dead", "mandate_broken", "risk_flagged", "integration_bug", "already_settled",
})

#: The intervention latent truth rewards for each family. Used to score whether
#: the agent picked the right *kind* of action, independently of whether the
#: seeded coin flip happened to land in its favour.
CORRECT_INTERVENTION = {
    "already_settled": {"suppress_case", "no_action"},
    "risk_flagged": {"escalate_to_merchant_ops", "no_action"},
    "merchant_config": {"escalate_to_merchant_ops", "no_action"},
    "integration_bug": {"open_engineering_ticket", "escalate_to_merchant_ops", "no_action"},
    "instrument_dead": {"send_instrument_update_link", "send_recovery_link", "no_action"},
    "mandate_broken": {"send_mandate_reauth_link", "send_reminder", "no_action"},
    "balance_dependent": {"retry_payment", "send_reminder",
                          "send_recovery_link", "offer_partial_payment"},
    "transient_infra": {"retry_payment", "send_recovery_link"},
    "limit_bound": {"retry_payment", "send_recovery_link"},
    "customer_dropout": {"send_recovery_link", "send_reminder"},
}

#: Actions that count as "we contacted this human".
CONTACT_ACTIONS = frozenset({
    "send_recovery_link", "send_instrument_update_link", "send_mandate_reauth_link",
    "send_reminder", "offer_partial_payment", "issue_discount", "escalate_to_collections",
})


def compute(conn: sqlite3.Connection, violations: list[dict] | None = None) -> dict:
    cases = [dict(r) for r in db.rows(conn, "SELECT * FROM cases")]
    actions = [dict(r) for r in db.rows(conn, "SELECT * FROM actions")]
    latent = {c["id"]: (jload(c["latent"], {}) or {}) for c in cases}

    at_risk = sum(c["amount_paise"] for c in cases)
    recoverable = sum(c["amount_paise"] for c in cases if latent[c["id"]].get("recoverable"))
    recovered = db.scalar(conn, "SELECT SUM(amount_paise) FROM ledger")
    recovered_cases = db.scalar(conn, "SELECT COUNT(DISTINCT case_id) FROM ledger")

    executed = [a for a in actions if a["executed_at"]]
    retries = [a for a in executed if a["action_type"] == "retry_payment"]
    contacts = [a for a in executed if a["action_type"] in CONTACT_ACTIONS]

    wasted_retries = [
        a for a in retries
        if latent[a["case_id"]].get("family") in ZERO_YIELD_RETRY_FAMILIES
    ]
    # The most damaging false positive in revenue recovery: messaging somebody who
    # has already paid.
    chased_already_paid = [
        a for a in contacts if latent[a["case_id"]].get("family") == "already_settled"
    ]
    contacted_opted_out = [
        a for a in contacts
        if db.scalar(conn, "SELECT contact_opt_out FROM customers WHERE id ="
                     " (SELECT customer_id FROM cases WHERE id=?)", (a["case_id"],))
    ]

    # First substantive action per case, scored against the family latent truth rewards.
    first_action: dict[str, str] = {}
    for a in sorted(actions, key=lambda x: x["proposed_at"]):
        if a["action_type"] != "no_action" and a["case_id"] not in first_action:
            first_action[a["case_id"]] = a["action_type"]
    scored = [(cid, act) for cid, act in first_action.items()
              if latent.get(cid, {}).get("family") in CORRECT_INTERVENTION]
    correct = [1 for cid, act in scored if act in CORRECT_INTERVENTION[latent[cid]["family"]]]

    diag_scored, diag_correct = _diagnosis_accuracy(conn, latent)

    by_state = Counter(c["state"] for c in cases)
    money_by_state = defaultdict(int)
    for c in cases:
        money_by_state[c["state"]] += c["amount_paise"]

    recovered_by_action = defaultdict(int)
    for r in db.rows(conn, "SELECT a.action_type, SUM(l.amount_paise) amt FROM ledger l"
                           " JOIN actions a ON a.id = l.action_id GROUP BY a.action_type"):
        recovered_by_action[r["action_type"]] = r["amt"]

    recovered_by_family = defaultdict(int)
    for c in cases:
        if c["recovered_paise"]:
            recovered_by_family[latent[c["id"]].get("family", "unknown")] += c["recovered_paise"]

    recovered_by_segment = defaultdict(int)
    for r in db.rows(conn, "SELECT cu.segment, SUM(l.amount_paise) amt FROM ledger l"
                           " JOIN cases c ON c.id=l.case_id JOIN customers cu"
                           " ON cu.id=c.customer_id GROUP BY cu.segment"):
        recovered_by_segment[r["segment"]] = r["amt"]

    approvals = db.rows(conn, "SELECT ap.*, a.action_type, c.amount_paise FROM approvals ap"
                              " JOIN actions a ON a.id=ap.action_id"
                              " JOIN cases c ON c.id=ap.case_id")

    return {
        "money": {
            "at_risk_paise": at_risk,
            "latently_recoverable_paise": recoverable,
            "recovered_paise": recovered,
            "recovery_rate_of_at_risk": _pct(recovered, at_risk),
            "recovery_rate_of_recoverable": _pct(recovered, recoverable),
            "held_for_approval_paise": sum(
                c["amount_paise"] for c in cases if c["state"] == "awaiting_approval"),
            "escalated_paise": money_by_state.get("escalated", 0),
            "unrecovered_paise": at_risk - recovered,
            "annualised_mrr_at_risk_paise": sum(c["mrr_paise"] for c in cases) * 12,
            "annualised_mrr_protected_paise": sum(
                c["mrr_paise"] for c in cases if c["state"] == "recovered") * 12,
        },
        "cases": {
            "total": len(cases),
            "recovered": recovered_cases,
            "by_state": dict(by_state),
            "money_by_state": dict(money_by_state),
            "all_terminal": all(
                c["state"] in ("recovered", "stopped", "escalated", "suppressed",
                               "awaiting_approval") for c in cases),
        },
        "actions": {
            "proposed": len(actions),
            "executed": len(executed),
            "allowed": sum(a["policy_decision"] == "allow" for a in actions),
            "blocked_by_policy": sum(a["policy_decision"] == "deny" for a in actions),
            "required_approval": sum(a["policy_decision"] == "require_approval" for a in actions),
            "not_executed": sum(a["outcome"] == "not_executed" for a in executed),
            "retries": len(retries),
            "contacts": len(contacts),
            "by_type": dict(Counter(a["action_type"] for a in executed)),
        },
        "quality": {
            "wasted_retries": len(wasted_retries),
            "wasted_retry_rate": _pct(len(wasted_retries), len(retries)),
            "customers_chased_after_paying": len(chased_already_paid),
            "opted_out_customers_contacted": len(contacted_opted_out),
            "intervention_accuracy": _pct(len(correct), len(scored)),
            "intervention_scored": len(scored),
            "diagnosis_accuracy": _pct(diag_correct, diag_scored),
            "diagnosis_scored": diag_scored,
            "actions_per_case": round(len(executed) / max(1, len(cases)), 2),
            "contacts_per_recovered_case": round(len(contacts) / max(1, recovered_cases), 2),
        },
        "compliance": {
            "rbi_contact_window_violations": _count(violations, "rbi_contact_window"),
            "npci_debit_window_violations": _count(violations, "npci_debit_window"),
            "customer_opt_out_violations": _count(violations, "customer_opt_out"),
            "total_violations": len(violations or []),
        },
        "stopping": {
            "by_reason": dict(Counter(
                c["stop_reason"] for c in cases if c["stop_reason"])),
            # If either of these is non-zero, a bound leaked. Asserted in the tests.
            "cases_over_retry_cap": sum(c["attempts"] > 3 for c in cases),
            "cases_over_contact_cap": sum(c["contacts_sent"] > 3 for c in cases),
        },
        "approvals": {
            "requested": len(approvals),
            "pending": sum(a["decided_at"] is None for a in approvals),
            "value_paise": sum(a["amount_paise"] for a in approvals),
            "items": [
                {"action_id": a["action_id"], "case_id": a["case_id"],
                 "action": a["action_type"], "amount_paise": a["amount_paise"],
                 "reason": a["reason"], "decision": a["decision"]}
                for a in approvals
            ],
        },
        "attribution": {
            "recovered_by_action": dict(recovered_by_action),
            "recovered_by_failure_family": dict(recovered_by_family),
            "recovered_by_segment": dict(recovered_by_segment),
        },
    }


def _diagnosis_accuracy(conn, latent) -> tuple[int, int]:
    """Score the first diagnosis per case against the latent true cause."""
    seen, correct = set(), 0
    for r in db.rows(conn, "SELECT case_id, detail FROM audit WHERE stage='diagnose'"
                           " ORDER BY seq"):
        cid = r["case_id"]
        if cid in seen or cid not in latent:
            continue
        seen.add(cid)
        got = (jload(r["detail"], {}) or {}).get("root_cause")
        correct += got == latent[cid].get("true_cause")
    return len(seen), correct


def _count(violations, rule) -> int:
    return sum(1 for v in (violations or []) if v["rule"] == rule)


def _pct(num, den) -> float:
    return round(100.0 * num / den, 2) if den else 0.0


def unretryable_share(conn) -> dict:
    """How much of the batch is structurally unretryable, straight off the taxonomy.

    This is the headline claim the product rests on, computed from the data rather
    than asserted: a fixed retry ladder spends attempts on all of it.
    """
    total = zero = 0
    zero_paise = total_paise = 0
    for c in db.rows(conn, "SELECT amount_paise, error_reason FROM cases"
                           " WHERE error_reason IS NOT NULL"):
        total += 1
        total_paise += c["amount_paise"]
        if lookup(c["error_reason"]).family in ZERO_YIELD_RETRY_FAMILIES:
            zero += 1
            zero_paise += c["amount_paise"]
    return {"cases_with_failure_code": total, "structurally_unretryable_cases": zero,
            "structurally_unretryable_share": _pct(zero, total),
            "structurally_unretryable_paise": zero_paise,
            "share_of_failed_value": _pct(zero_paise, total_paise)}
