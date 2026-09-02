"""End-to-end behaviour of the loop, and the invariants around money."""

from munshi import audit, db
from munshi.adapters.simulator import SimulatorAdapter
from munshi.clock import VirtualClock
from munshi.models import CaseState
from munshi.orchestrator import Orchestrator
from munshi.reason import HeuristicReasoner
from munshi.seed.generate import BATCH_START
from munshi.seed.load import load
from tests.conftest import make_case


def run(conn, days=14, **kw):
    o = Orchestrator(conn, HeuristicReasoner(), SimulatorAdapter(),
                     VirtualClock(BATCH_START), **kw)
    stats = o.run(days=days, step_hours=2)
    return o, stats


def test_every_case_reaches_a_terminal_state(seeded):
    run(seeded)
    open_cases = db.rows(
        seeded, "SELECT id, state FROM cases WHERE state NOT IN (?,?,?,?,?)",
        (*CaseState.TERMINAL, CaseState.AWAITING_APPROVAL))
    assert open_cases == [], f"cases left in limbo: {[dict(r) for r in open_cases]}"


def test_recovered_money_always_has_a_ledger_row(seeded):
    """The only claim of recovery this system makes is one it can point at."""
    run(seeded)
    for c in db.rows(seeded, "SELECT id, recovered_paise FROM cases WHERE recovered_paise > 0"):
        ledger = db.scalar(seeded, "SELECT SUM(amount_paise) FROM ledger WHERE case_id=?",
                           (c["id"],))
        assert ledger == c["recovered_paise"]


def test_every_ledger_row_points_at_an_executed_action(seeded):
    run(seeded)
    orphans = db.rows(seeded,
                      "SELECT l.id FROM ledger l LEFT JOIN actions a ON a.id = l.action_id"
                      " WHERE a.id IS NULL OR a.executed_at IS NULL")
    assert orphans == []


def test_recovered_total_never_exceeds_amount_at_risk(seeded):
    run(seeded)
    assert (db.scalar(seeded, "SELECT SUM(amount_paise) FROM ledger")
            <= db.scalar(seeded, "SELECT SUM(amount_paise) FROM cases"))


def test_no_case_exceeds_its_budgets(seeded):
    run(seeded)
    assert db.scalar(seeded, "SELECT COUNT(*) FROM cases WHERE attempts > 3") == 0
    assert db.scalar(seeded, "SELECT COUNT(*) FROM cases WHERE contacts_sent > 3") == 0


def test_audit_chain_survives_a_full_batch(seeded):
    run(seeded)
    out = audit.verify(seeded)
    assert out["valid"] and out["checked"] > 100


def test_every_executed_action_produced_an_audit_record(seeded):
    run(seeded)
    executed = db.scalar(seeded, "SELECT COUNT(*) FROM actions WHERE executed_at IS NOT NULL")
    audited = db.scalar(seeded, "SELECT COUNT(DISTINCT action_id) FROM audit"
                                " WHERE stage='execute' AND action_id IS NOT NULL")
    assert audited == executed


def test_run_is_reproducible(tmp_path):
    """Same seed, same batch, same result. Without this the evaluation means nothing."""
    totals = []
    for i in range(2):
        c = db.reset(tmp_path / f"r{i}.db")
        load(c, n=50, seed=99)
        run(c)
        totals.append(db.scalar(c, "SELECT SUM(amount_paise) FROM ledger"))
        c.close()
    assert totals[0] == totals[1]


def test_approval_gated_actions_stay_parked_without_a_human(conn):
    case = make_case(conn, amount_paise=90_000 * 100, error_reason="insufficient_funds",
                     opened_at=BATCH_START - 30 * 3600)
    o, _ = run(conn, days=3)
    row = db.one(conn, "SELECT state FROM cases WHERE id=?", (case["id"],))
    pending = db.scalar(conn, "SELECT COUNT(*) FROM approvals WHERE decided_at IS NULL")
    assert row["state"] == CaseState.AWAITING_APPROVAL
    assert pending == 1
    # And nothing was executed on the merchant's behalf.
    assert db.scalar(conn, "SELECT COUNT(*) FROM ledger WHERE case_id=?", (case["id"],)) == 0


def test_rejecting_an_approval_stops_the_case(conn):
    make_case(conn, amount_paise=90_000 * 100, error_reason="insufficient_funds",
              opened_at=BATCH_START - 30 * 3600)
    o, _ = run(conn, days=3)
    action_id = db.one(conn, "SELECT action_id FROM approvals LIMIT 1")["action_id"]
    o.reject(action_id, BATCH_START + 100, decided_by="tester")
    row = db.one(conn, "SELECT state, stop_reason FROM cases WHERE id='case_t1'")
    assert row["state"] == CaseState.STOPPED
    assert "rejected_by_merchant" in row["stop_reason"]


def test_a_replayed_event_does_not_produce_a_second_recovery(seeded):
    """At-least-once delivery must not become at-least-once charging."""
    run(seeded)
    dupes = db.rows(seeded, "SELECT idempotency_key, COUNT(*) n FROM actions"
                            " WHERE executed_at IS NOT NULL GROUP BY idempotency_key"
                            " HAVING n > 1")
    assert dupes == []
