"""Invariants the reported numbers depend on."""

from munshi import db
from munshi.evaluation.harness import run_arm
from munshi.evaluation.metrics import ZERO_YIELD_RETRY_FAMILIES, compute, unretryable_share
from munshi.seed.generate import build


def test_batch_generation_is_deterministic():
    import json

    a, b = build(n=40, seed=5), build(n=40, seed=5)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_batch_contains_every_recovery_family():
    from munshi.db import jload

    batch = build(n=320)
    families = {jload(c["latent"])["family"] for c in batch["cases"]}
    # A batch missing a family would silently stop exercising a whole branch.
    assert families >= {"balance_dependent", "customer_dropout", "transient_infra",
                        "instrument_dead", "risk_flagged", "already_settled",
                        "merchant_config", "mandate_broken", "limit_bound"}


def test_the_agent_never_sees_latent_ground_truth():
    """If the context pack ever leaked latent state, every accuracy number here
    would be meaningless."""
    from munshi.enrich import build_context

    c = db.reset("/tmp/leak.db")
    from munshi.seed.load import load

    load(c, n=20, seed=3)
    for row in db.rows(c, "SELECT * FROM cases"):
        blob = repr(build_context(c, row, 1_787_540_000))
        for secret in ("funds_available_after_h", "will_replace_instrument", "recoverable",
                       "outage_clears_after_h", "responds_to_contact", "true_cause",
                       "honours_promise", "resolves_at"):
            assert secret not in blob, f"{secret} leaked into the agent's context"
    c.close()


def test_agent_wastes_no_retries_and_the_baseline_does():
    """The product's core claim, asserted rather than narrated."""
    agent = run_arm("agent-heuristic", n=160, seed=11, days=14, step_hours=2)
    base = run_arm("baseline", n=160, seed=11, days=14, step_hours=2)
    assert agent["quality"]["wasted_retries"] == 0
    assert base["quality"]["wasted_retries"] > 0
    assert agent["quality"]["customers_chased_after_paying"] == 0
    assert agent["quality"]["opted_out_customers_contacted"] == 0
    assert agent["compliance"]["total_violations"] == 0
    assert base["compliance"]["total_violations"] > 0
    assert agent["quality"]["intervention_accuracy"] > base["quality"]["intervention_accuracy"]


def test_bounds_hold_in_both_arms():
    for arm in ("agent-heuristic", "baseline"):
        m = run_arm(arm, n=120, seed=13, days=14, step_hours=2)
        assert m["stopping"]["cases_over_retry_cap"] == 0, arm
        assert m["stopping"]["cases_over_contact_cap"] == 0, arm
        assert m["run"]["audit"]["valid"], arm


def test_recovered_never_exceeds_latently_recoverable():
    """Money can only be recovered from cases where it was there to recover."""
    for arm in ("agent-heuristic", "baseline"):
        m = run_arm(arm, n=160, seed=17, days=14, step_hours=2)
        assert m["money"]["recovered_paise"] <= m["money"]["latently_recoverable_paise"], arm


def test_unretryable_share_is_material():
    c = db.reset("/tmp/share.db")
    from munshi.seed.load import load

    load(c, n=320, seed=20260824)
    share = unretryable_share(c)
    assert share["structurally_unretryable_share"] > 20
    assert share["structurally_unretryable_cases"] > 40
    c.close()


def test_metrics_report_zero_not_crash_on_an_empty_run(conn):
    m = compute(conn, [])
    assert m["money"]["recovered_paise"] == 0
    assert m["quality"]["wasted_retry_rate"] == 0.0
    assert ZERO_YIELD_RETRY_FAMILIES
