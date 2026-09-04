"""Deterministic provider that drives the real agent loop without a network.

This is a test double, not a pretend model. It exists so that the tool loop --
dispatch, argument validation, error handling, the terminal decision -- is
exercised end to end in CI and on a machine with no credential, and so that
adversarial behaviour (malformed arguments, an invented tool, a model that never
decides) can be reproduced exactly rather than waited for.

It is labelled `mock` everywhere it surfaces. Nothing in the product presents its
output as a model's.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from .base import LLMTurn, ToolCall

#: Which action a brief's failure family calls for. Deliberately small: the mock
#: is a stand-in for a model's judgement, not a second implementation of it.
_ACTION_BY_FAMILY = {
    "already_settled": "suppress_case",
    "risk_flagged": "escalate_to_merchant_ops",
    "merchant_config": "escalate_to_merchant_ops",
    "integration_bug": "open_engineering_ticket",
    "instrument_dead": "send_instrument_update_link",
    "mandate_broken": "send_mandate_reauth_link",
    "customer_dropout": "send_recovery_link",
    "transient_infra": "retry_payment",
    "balance_dependent": "retry_payment",
    "limit_bound": "retry_payment",
}
_CAUSE_BY_FAMILY = {
    "already_settled": "already_paid",
    "risk_flagged": "risk_decline",
    "merchant_config": "merchant_misconfiguration",
    "integration_bug": "integration_defect",
    "instrument_dead": "instrument_dead",
    "mandate_broken": "mandate_invalid",
    "customer_dropout": "customer_abandoned",
    "transient_infra": "gateway_transient",
    "balance_dependent": "payer_balance",
    "limit_bound": "limit_exhausted",
}


class MockProvider:
    """Walks a fixed tool sequence, then submits a taxonomy-consistent decision.

    `script` overrides the sequence entirely, which is how the adversarial tests
    inject a malformed tool call, an unknown tool, or a model that never decides.
    """

    name = "mock"

    def __init__(self, model: str = "mock-deterministic",
                 script: list[Callable[[list[dict]], LLMTurn]] | None = None):
        self.model = model
        self._script = script
        self.calls = 0

    def chat(self, messages, tools=None, *, tool_choice="auto", max_tokens=2048,
             temperature=0.0) -> LLMTurn:
        # Derived from the conversation, not from instance state. One provider is
        # shared across concurrently-decided cases, so a counter on `self` would
        # interleave their turn indices and give later cases a truncated loop.
        turn_index = sum(1 for m in messages if m.get("role") == "assistant")
        self.calls += 1

        if self._script is not None:
            turn_index = self.calls - 1  # scripted runs are single-conversation by design
            if turn_index >= len(self._script):
                return _turn(text="no further scripted turns")
            return self._script[turn_index](messages)

        brief = _brief(messages)
        if turn_index == 0:
            # Look at the payer and the rail before deciding anything.
            return _turn(calls=[
                ToolCall("c0", "get_customer_context", {"case_id": brief.get("case_id", "")}),
                ToolCall("c1", "get_downtime_status", {"case_id": brief.get("case_id", "")}),
            ])
        if turn_index == 1:
            # Dry-run the candidate against policy before committing to it.
            return _turn(calls=[ToolCall("c2", "check_policy", {
                "case_id": brief.get("case_id", ""),
                "action_type": _action_for(brief),
                "delay_hours": _delay_for(brief),
            })])
        return _turn(calls=[ToolCall("c3", "submit_decision", _decision(brief, messages))])


def _turn(text: str | None = None, calls: list[ToolCall] | None = None) -> LLMTurn:
    calls = calls or []
    raw: dict = {"role": "assistant", "content": text or ""}
    if calls:
        raw["tool_calls"] = [
            {"id": c.id, "type": "function",
             "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
            for c in calls
        ]
    return LLMTurn(text=text, tool_calls=calls,
                   finish_reason="tool_calls" if calls else "stop", raw_message=raw)


def _brief(messages: list[dict]) -> dict:
    """The case brief is the first user message, sent as JSON precisely so a
    deterministic stand-in can read it the same way a model does."""
    for m in messages:
        if m.get("role") == "user":
            try:
                value = json.loads(m.get("content") or "{}")
            except json.JSONDecodeError:
                return {}
            return value if isinstance(value, dict) else {}
    return {}


def _family(brief: dict) -> str:
    return (brief.get("failure") or {}).get("family", "transient_infra")


def _action_for(brief: dict) -> str:
    kind = (brief.get("case") or {}).get("kind")
    if kind == "invoice_overdue":
        return "send_reminder"
    if kind == "checkout_abandoned":
        return "send_recovery_link"
    return _ACTION_BY_FAMILY.get(_family(brief), "retry_payment")


def _delay_for(brief: dict) -> float:
    fail = brief.get("failure") or {}
    age = float((brief.get("case") or {}).get("age_hours") or 0)
    floor = float(fail.get("min_backoff_hours") or 0)
    return round(max(0.0, floor - age), 1)


def _decision(brief: dict, messages: list[dict]) -> dict:
    family = _family(brief)
    action = _action_for(brief)
    # A dry-run refusal only means give up when it is permanent. A deny carrying
    # `would_reschedule_to_hours` means "not yet", and abandoning the case there
    # writes off revenue that was only waiting on a cooldown or a contact window.
    for m in reversed(messages):
        if m.get("role") == "tool" and "check_policy" in (m.get("name") or ""):
            try:
                verdict = json.loads(m.get("content") or "{}")
            except json.JSONDecodeError:
                verdict = {}
            failed = {r.get("rule") for r in (verdict.get("failed_rules") or [])}
            # Read the failing rule and take the action that unblocks it, which is
            # the entire reason check_policy exists.
            if "emandate_pre_debit_notice" in failed:
                action = "send_reminder"
            elif "retry_can_succeed" in failed:
                action = _ACTION_BY_FAMILY.get(_family(brief), "no_action")
                if action == "retry_payment":
                    action = "no_action"
            elif (verdict.get("decision") == "deny"
                  and verdict.get("would_reschedule_to_hours") is None):
                action = "no_action"
            break
    contacts = "link" in action or "reminder" in action
    return {
        "root_cause": _CAUSE_BY_FAMILY.get(family, "unknown"),
        "confidence": 0.65,
        "recoverability": 0.5,
        "diagnosis_rationale": f"Mock provider: taxonomy family {family}.",
        "evidence": [f"family={family}"],
        "action_type": action,
        "delay_hours": _delay_for(brief),
        "channel": "email" if contacts else "none",
        "message": "Your payment could not be completed. You can complete it using this link."
                   if contacts else "",
        "justification": f"Mock provider default intervention for {family}.",
    }
