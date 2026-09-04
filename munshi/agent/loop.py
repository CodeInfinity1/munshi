"""The bounded tool-calling loop.

The agent gets a case brief and a set of read-only tools, decides for itself what
else it needs to look at, and ends by calling `submit_decision`. What comes back
is a *proposal*: the deterministic policy engine and the executor take it from
there, and neither of them trusts it.

Three bounds make this safe to run unattended over hundreds of cases:

- **Turn cap.** An agent that will not decide has to stop costing money, so the
  loop is bounded rather than open-ended. Exhausting it degrades to the
  deterministic reasoner.
- **No write tools.** Nothing the model can call moves money or reaches a
  customer. See `tools.py`.
- **Every failure degrades, never propagates.** A timeout, a rate limit, an
  invented tool, a malformed argument, a refusal to decide -- all of them end in
  the deterministic path with the reason recorded. An LLM failure must never be
  able to corrupt financial state, so it never touches it.

The whole trace is kept and written into the audit record, which is also what the
dashboard renders: you can watch which tools it reached for and what came back.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from ..llm.base import LLMError, LLMMalformed, LLMRateLimited, LLMTurn
from .tools import TOOL_SPECS, Toolbox

log = logging.getLogger("munshi.agent")

#: Tool results are truncated before going back into context. A 25-case payment
#: history is useful; the same history rendered in full is just context pressure.
MAX_TOOL_RESULT_CHARS = 2400


class AgentFailed(LLMError):
    """The loop could not obtain a usable decision. Always degrades."""


SYSTEM = """You are the reasoning core of Munshi, a revenue-recovery agent for Indian \
merchants on Razorpay. You are given ONE revenue-risk case and a set of read-only tools. \
Investigate as much or as little as the case warrants, then call `submit_decision` exactly \
once.

WHAT YOU ARE AND ARE NOT DOING
- You do NOT execute anything. `submit_decision` proposes an action; a deterministic \
policy engine then approves, gates or refuses it, and an executor carries out whatever \
survives. You cannot move money, and you should not try.
- Retryability is NOT your call. `get_failure_semantics` returns Razorpay's documented \
position on whether a retry on this instrument can ever succeed. When it says futile, \
propose the action that changes the precondition instead.
- Limits, cooldowns, contact windows and ceilings are NOT your call either. \
`check_policy` will tell you what the engine would say. Use it before proposing anything \
you are unsure about; it costs nothing and consumes nothing.

WHERE YOUR JUDGEMENT ACTUALLY MATTERS
1. Ambiguous failure codes. Razorpay documents `payment_failed` as "no specific error \
code received from gateway", and `card_declined` and `payment_declined` are similarly \
opaque. Whether a given decline is an outage, a balance problem or a dying instrument is \
a weighing of downtime state, payer history, amount and age. Reach for the tools.
2. Which intervention, and WHEN. For balance failures the whole game is timing. \
`delay_hours` is measured from now, but preconditions are measured from when the failure \
happened -- check `case.age_hours` before you wait.
3. Whether this is worth chasing at all. Proposing `no_action` on a case with low \
recoverability is a correct and valuable answer, not a failure to engage.
4. The customer-facing message, when one is warranted.

MESSAGE STYLE. Plain, specific, Indian-English business register. Reference the real \
reason and the real amount. No emoji, no guilt, no fake urgency, and never invent an \
offer, a discount, a deadline or an account detail. Empty string when the action does not \
contact anyone.

`diagnosis_rationale` and `justification` are written into a merchant-visible audit trail. \
One or two sentences of stated reasoning each -- what you concluded and why, not how you \
got there."""


@dataclass(slots=True)
class AgentTrace:
    provider: str
    model: str
    turns: int = 0
    tool_calls: list[dict] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    outcome: str = "decided"          # decided | salvaged | degraded
    degrade_reason: str | None = None
    retries: int = 0

    def summary(self) -> dict:
        """What goes into the audit record and onto the dashboard."""
        return {
            "provider": self.provider,
            "model": self.model,
            "turns": self.turns,
            "outcome": self.outcome,
            "degrade_reason": self.degrade_reason,
            "tokens": {"prompt": self.prompt_tokens, "completion": self.completion_tokens},
            "tools_used": [c["tool"] for c in self.tool_calls],
            "tool_calls": self.tool_calls,
        }


def run_agent(provider, toolbox: Toolbox, brief: dict, *, max_turns: int = 6
              ) -> tuple[dict, AgentTrace]:
    """Run the loop. Returns the raw `submit_decision` arguments, unvalidated.

    Validation belongs to the caller: the loop's job is to obtain a proposal, not
    to decide whether it is acceptable.
    """
    trace = AgentTrace(provider=provider.name, model=provider.model)
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM},
        # JSON, not prose: the brief is data, and a deterministic stand-in has to
        # be able to read it exactly the way a model does.
        {"role": "user", "content": json.dumps(brief, separators=(",", ":"))},
    ]
    nudged = False

    for _ in range(max_turns):
        turn = _chat_with_one_retry(provider, messages, trace)
        trace.turns += 1
        trace.prompt_tokens += turn.prompt_tokens
        trace.completion_tokens += turn.completion_tokens
        messages.append(turn.raw_message or {"role": "assistant", "content": turn.text or ""})

        if turn.wants_tools:
            decision = _dispatch(turn, toolbox, messages, trace)
            if decision is not None:
                return decision, trace
            continue

        # No tool call. The model may have answered in prose with the decision
        # embedded, which gpt-oss models do intermittently.
        salvaged = _salvage(turn)
        if salvaged is not None:
            trace.outcome = "salvaged"
            trace.tool_calls.append(
                {"tool": "submit_decision", "arguments": salvaged,
                 "result": {"note": "recovered from prose; the model did not use the tool"}})
            return salvaged, trace

        if nudged:
            raise AgentFailed("model produced neither a tool call nor a usable decision")
        nudged = True
        messages.append({
            "role": "user",
            "content": "You must finish by calling the submit_decision tool. Call it now "
                       "with your decision.",
        })

    raise AgentFailed(f"no decision within {max_turns} turns")


def _dispatch(turn: LLMTurn, toolbox: Toolbox, messages: list[dict],
              trace: AgentTrace) -> dict | None:
    """Run the requested tools. Returns the decision if the run is over."""
    decision: dict | None = None
    for call in turn.tool_calls:
        if call.name == "submit_decision":
            decision = call.arguments
            trace.tool_calls.append({"tool": call.name, "arguments": call.arguments,
                                     "result": {"accepted_for_validation": True}})
            continue
        result = toolbox.call(call.name, call.arguments)
        trace.tool_calls.append({"tool": call.name, "arguments": call.arguments,
                                 "result": _shrink(result)})
        messages.append({
            "role": "tool",
            "tool_call_id": call.id,
            "name": call.name,
            "content": _serialise(result),
        })
    return decision


def _chat_with_one_retry(provider, messages, trace: AgentTrace) -> LLMTurn:
    """A rate limit is worth exactly one bounded retry. Everything else is not:
    retrying a malformed response or a rejected credential just spends money."""
    try:
        return provider.chat(messages, TOOL_SPECS, max_tokens=2048)
    except LLMRateLimited as exc:
        trace.retries += 1
        log.warning("groq rate limited, retrying once: %s", exc)
        time.sleep(2.0)
        return provider.chat(messages, TOOL_SPECS, max_tokens=2048)


def _salvage(turn: LLMTurn) -> dict | None:
    from ..llm.groq_provider import salvage_json

    value = salvage_json(turn.text)
    # Only accept something that is at least shaped like a decision. A stray JSON
    # object in prose is not a decision.
    if isinstance(value, dict) and "action_type" in value and "root_cause" in value:
        return value
    return None


def _serialise(result: dict) -> str:
    text = json.dumps(result, separators=(",", ":"), default=str)
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    return text[:MAX_TOOL_RESULT_CHARS] + '..."truncated":true}'


def _shrink(result: dict) -> dict:
    """Trace copy: keep it small enough to store on every case."""
    text = json.dumps(result, default=str)
    if len(text) <= 900:
        return result
    return {"_truncated": True, "preview": text[:900]}


_ = LLMMalformed  # re-exported for callers that catch it alongside AgentFailed
