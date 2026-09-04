"""The agent loop, its tools, and every way it can go wrong.

The premise throughout is that the model is hostile or broken. Each test either
drives the real loop with a scripted provider that misbehaves, or checks that a
tool cannot be used to do something a tool should not be able to do.
"""

import json

import pytest

from munshi.agent.loop import AgentFailed, run_agent
from munshi.agent.tools import TOOL_NAMES, TOOL_SPECS, Toolbox
from munshi.enrich import build_context
from munshi.llm.base import (
    LLMMalformed,
    LLMRateLimited,
    LLMTimeout,
    LLMTurn,
    LLMUnavailable,
    ToolCall,
)
from munshi.llm.mock_provider import MockProvider
from munshi.models import ACTION_TIERS, ROOT_CAUSES
from munshi.policy import PolicyEngine
from munshi.reason import AgentReasoner, build_brief
from munshi.seed.generate import BATCH_START
from tests.conftest import make_case

GOOD = {
    "root_cause": "payer_balance", "confidence": 0.8, "recoverability": 0.7,
    "diagnosis_rationale": "Balance shortfall.", "evidence": ["insufficient_funds"],
    "action_type": "retry_payment", "delay_hours": 20, "channel": "none", "message": "",
    "justification": "Wait for the balance to top up.",
}


def _turn(calls=None, text=None):
    calls = calls or []
    return LLMTurn(text=text, tool_calls=calls,
                   finish_reason="tool_calls" if calls else "stop",
                   raw_message={"role": "assistant", "content": text or ""})


class Scripted:
    """A provider that does exactly what the test tells it to."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, *turns, raises=None):
        self._turns = list(turns)
        self._raises = raises
        self.calls = 0

    def chat(self, messages, tools=None, **kw):
        self.calls += 1
        if self._raises:
            raise self._raises
        if not self._turns:
            return _turn(text="nothing more to say")
        return self._turns.pop(0)


@pytest.fixture
def box(conn):
    case = make_case(conn)
    ctx = build_context(conn, case, BATCH_START)
    eng = PolicyEngine(conn)
    eng.begin_run()
    return Toolbox(conn, case, ctx, BATCH_START, eng), ctx


# ---------------------------------------------------------------------------
# The tool surface
# ---------------------------------------------------------------------------
def test_no_tool_can_move_money_or_reach_a_customer():
    """The whole safety argument for handing a model tools rests on this."""
    forbidden = {"retry_payment", "create_payment_link", "send_recovery_message",
                 "send_message", "issue_discount", "write_off", "execute_action",
                 "charge", "refund"}
    assert not (TOOL_NAMES & forbidden), TOOL_NAMES & forbidden
    # The only terminal tool proposes; it does not act.
    assert "submit_decision" in TOOL_NAMES


def test_tool_schemas_are_wire_valid():
    for t in TOOL_SPECS:
        wire = t.to_openai()
        assert wire["type"] == "function"
        assert wire["function"]["parameters"]["type"] == "object"
        assert set(wire["function"]["parameters"]["required"]) <= set(
            wire["function"]["parameters"]["properties"])
        json.dumps(wire)


def test_submit_decision_enums_match_the_policy_vocabulary():
    """If these drift, the agent can propose something the engine has never heard of."""
    props = next(t for t in TOOL_SPECS if t.name == "submit_decision").parameters["properties"]
    assert set(props["action_type"]["enum"]) == set(ACTION_TIERS)
    assert set(props["root_cause"]["enum"]) == set(ROOT_CAUSES)


def test_read_tools_return_data(box):
    tb, _ = box
    assert tb.call("get_customer_context", {})["segment"] == "consumer"
    assert "attempts_remaining" in tb.call("get_recovery_history", {})
    assert tb.call("get_failure_semantics", {"error_reason": "card_expired"})[
        "retry_on_same_instrument_is_futile"] is True
    assert "expected_recoverable_paise" in tb.call("calculate_recovery_score", {})
    assert tb.call("get_downtime_status", {})["state"] in (
        "clear", "active", "scheduled", "recently_resolved", "not_applicable")


def test_unknown_tool_is_answered_not_raised(box):
    tb, _ = box
    out = tb.call("transfer_funds", {"amount": 100000})
    assert "error" in out and "available" in out


def test_bad_tool_arguments_are_answered_not_raised(box):
    tb, _ = box
    assert "error" in tb.call("get_failure_semantics", {"wrong_arg": 1})
    assert "error" in tb.call("check_policy", {"action_type": "obliterate_invoice"})


def test_unknown_failure_code_returns_a_conservative_fallback(box):
    tb, _ = box
    out = tb.call("get_failure_semantics", {"error_reason": "brand_new_code"})
    assert "error" in out
    assert out["fallback"]["retry_on_same_instrument_is_futile"] is False
    assert out["fallback"]["min_backoff_hours"] >= 12


def test_check_policy_is_a_dry_run_that_consumes_nothing(box):
    """Consulting the policy must never be a way to spend against it."""
    tb, _ = box
    before = tb.policy.run_exposure_paise
    for _ in range(5):
        out = tb.call("check_policy", {"action_type": "retry_payment", "delay_hours": 0})
        assert out["decision"] in ("allow", "deny", "require_approval")
    assert tb.policy.run_exposure_paise == before


def test_check_policy_reports_the_same_verdict_the_engine_will_apply(conn):
    case = make_case(conn, error_reason="card_expired")
    ctx = build_context(conn, case, BATCH_START)
    eng = PolicyEngine(conn)
    eng.begin_run()
    tb = Toolbox(conn, case, ctx, BATCH_START, eng)
    dry = tb.call("check_policy", {"action_type": "retry_payment"})
    assert dry["decision"] == "deny"
    assert any(r["rule"] == "retry_can_succeed" for r in dry["failed_rules"])


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------
def test_a_well_behaved_agent_investigates_then_decides(box):
    tb, ctx = box
    provider = MockProvider()
    raw, trace = run_agent(provider, tb, build_brief(ctx), max_turns=6)
    assert raw["action_type"] in ACTION_TIERS
    assert trace.outcome == "decided"
    assert trace.turns == 3
    # It actually used its tools rather than deciding blind.
    assert {"get_customer_context", "get_downtime_status", "check_policy"} <= set(
        trace.summary()["tools_used"])


def test_the_loop_is_bounded(box):
    """An agent that will not decide has to stop costing money."""
    tb, ctx = box
    forever = Scripted(*[_turn([ToolCall(f"t{i}", "get_customer_context", {})])
                         for i in range(20)])
    with pytest.raises(AgentFailed, match="no decision within"):
        run_agent(forever, tb, build_brief(ctx), max_turns=4)
    assert forever.calls == 4


def test_a_silent_model_is_nudged_once_then_abandoned(box):
    tb, ctx = box
    silent = Scripted(_turn(text="I think we should probably retry."),
                      _turn(text="Yes, definitely retry."))
    with pytest.raises(AgentFailed, match="neither a tool call nor a usable decision"):
        run_agent(silent, tb, build_brief(ctx), max_turns=6)
    assert silent.calls == 2, "exactly one nudge, then give up"


def test_a_decision_returned_as_prose_is_salvaged(box):
    """gpt-oss models intermittently answer a structured request in text."""
    tb, ctx = box
    prose = Scripted(_turn(text=f"Here is my decision:\n{json.dumps(GOOD)}\nHope that helps."))
    raw, trace = run_agent(prose, tb, build_brief(ctx), max_turns=6)
    assert raw["action_type"] == "retry_payment"
    assert trace.outcome == "salvaged"


def test_stray_json_in_prose_is_not_mistaken_for_a_decision(box):
    tb, ctx = box
    noise = Scripted(_turn(text='I looked at {"foo": 1} and thought about it.'),
                     _turn(text="still thinking"))
    with pytest.raises(AgentFailed):
        run_agent(noise, tb, build_brief(ctx), max_turns=6)


def test_a_rate_limit_is_retried_exactly_once(box):
    tb, ctx = box

    class Flaky:
        name, model = "flaky", "flaky-1"

        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools=None, **kw):
            self.calls += 1
            if self.calls == 1:
                raise LLMRateLimited("429")
            return _turn([ToolCall("d", "submit_decision", GOOD)])

    p = Flaky()
    raw, trace = run_agent(p, tb, build_brief(ctx), max_turns=6)
    assert raw["action_type"] == "retry_payment"
    assert p.calls == 2 and trace.retries == 1


@pytest.mark.parametrize("exc", [LLMTimeout("slow"), LLMUnavailable("no key"),
                                 LLMMalformed("garbage")])
def test_provider_failures_propagate_as_llm_errors_not_crashes(box, exc):
    tb, ctx = box
    with pytest.raises(type(exc)):
        run_agent(Scripted(raises=exc), tb, build_brief(ctx), max_turns=6)


def test_the_brief_is_smaller_than_the_full_context(box):
    """If the brief contained everything, the tools would be decorative."""
    tb, ctx = box
    brief = build_brief(ctx)
    assert "customer" not in brief and "downtime" not in brief and "history" not in brief
    assert len(json.dumps(brief)) < len(json.dumps(ctx))


def test_the_brief_never_carries_latent_ground_truth(box):
    tb, ctx = box
    blob = json.dumps(build_brief(ctx))
    for secret in ("funds_available_after_h", "true_cause", "recoverable",
                   "will_replace_instrument", "responds_to_contact"):
        assert secret not in blob, secret


# ---------------------------------------------------------------------------
# The reasoner around the loop
# ---------------------------------------------------------------------------
def test_every_agent_failure_degrades_and_is_counted(box):
    """A model failure must never reach financial state."""
    tb, ctx = box
    for exc in (LLMTimeout("t"), LLMUnavailable("u"), LLMMalformed("m"),
                LLMRateLimited("r")):
        r = AgentReasoner(provider=Scripted(raises=exc))
        diag, plan = r.decide(ctx, tb)
        assert plan.action_type in ACTION_TIERS
        assert diag.reasoner == "heuristic"
        assert "agent unavailable" in diag.rationale
        assert r.degraded == 1
        assert r.last_trace["outcome"] == "degraded"


def test_an_invented_action_is_rejected_and_degrades(box):
    tb, ctx = box
    r = AgentReasoner(provider=Scripted(
        _turn([ToolCall("d", "submit_decision",
                        {**GOOD, "action_type": "wire_funds_to_agent"})])))
    diag, plan = r.decide(ctx, tb)
    assert plan.action_type in ACTION_TIERS
    assert diag.reasoner == "heuristic" and r.degraded == 1


def test_an_invented_root_cause_is_rejected(box):
    tb, ctx = box
    r = AgentReasoner(provider=Scripted(
        _turn([ToolCall("d", "submit_decision", {**GOOD, "root_cause": "vibes"})])))
    diag, _ = r.decide(ctx, tb)
    assert diag.root_cause in ROOT_CAUSES and r.degraded == 1


def test_out_of_range_numbers_are_clamped_not_trusted(box):
    tb, ctx = box
    r = AgentReasoner(provider=Scripted(_turn([ToolCall("d", "submit_decision", {
        **GOOD, "confidence": 99, "recoverability": -5, "delay_hours": 1e9})])))
    diag, plan = r.decide(ctx, tb)
    assert 0 <= diag.confidence <= 1 and 0 <= diag.recoverability <= 1
    assert 0 <= plan.delay_hours <= 336
    assert r.degraded == 0, "clamping is not a failure"


def test_a_reasoner_without_tools_refuses_to_pretend_it_is_an_agent(box):
    _, ctx = box
    r = AgentReasoner(provider=MockProvider())
    diag, _ = r.decide(ctx, toolbox=None)
    assert diag.reasoner == "heuristic"
    assert "no toolbox" in diag.rationale


def test_a_valid_agent_decision_is_stamped_with_the_provider(box):
    tb, ctx = box
    r = AgentReasoner(provider=Scripted(_turn([ToolCall("d", "submit_decision", GOOD)])))
    diag, plan = r.decide(ctx, tb)
    assert diag.reasoner == "agent" and diag.model == "scripted-1"
    assert plan.params["agent_trace"]["outcome"] == "decided"
    assert r.degraded == 0
