"""Groq provider.

Uses tool calling rather than `response_format: json_schema` as the primary
structured-output mechanism. Two reasons: it is the mechanism `gpt-oss-120b` is
most reliable at (json_schema mode on this model has known cases of being
ignored and prose returned instead), and it is what makes the agent genuinely
tool-using rather than a single templated call.

Every failure mode is mapped onto Munshi's own error classes so the agent loop
can treat them differently. Nothing here is allowed to raise a raw provider
exception into the recovery loop.
"""

from __future__ import annotations

import json
import logging
import re

from ..config import settings
from .base import (
    LLMMalformed,
    LLMRateLimited,
    LLMTimeout,
    LLMTurn,
    LLMUnavailable,
    ToolCall,
)

log = logging.getLogger("munshi.llm.groq")

#: Last-resort recovery when a model returns a JSON object as prose instead of a
#: tool call. Used only to salvage a decision, never to invent one.
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class GroqProvider:
    name = "groq"

    def __init__(self, client=None):
        s = settings()
        if client is None and not s.groq_api_key:
            raise LLMUnavailable("GROQ_API_KEY is not set")
        self.model = s.groq_model
        self._effort = s.groq_reasoning_effort
        if client is not None:
            self._client = client
            return
        try:
            from groq import Groq
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise LLMUnavailable("the groq package is not installed") from exc
        self._client = Groq(
            api_key=s.groq_api_key,
            timeout=s.llm_timeout_seconds,
            # One SDK-level retry on transient failures; the agent loop owns the
            # decision about whether to keep going after that.
            max_retries=1,
        )

    def chat(self, messages, tools=None, *, tool_choice="auto", max_tokens=2048,
             temperature=0.0) -> LLMTurn:
        kwargs = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = [t.to_openai() for t in tools]
            kwargs["tool_choice"] = tool_choice
        if self._effort:
            kwargs["reasoning_effort"] = self._effort

        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - classified below, never propagated raw
            raise _classify(exc) from exc

        return self._parse(resp)

    def _parse(self, resp) -> LLMTurn:
        try:
            choice = resp.choices[0]
            msg = choice.message
        except (AttributeError, IndexError) as exc:
            raise LLMMalformed(f"provider returned no choices: {exc}") from exc

        calls: list[ToolCall] = []
        for tc in getattr(msg, "tool_calls", None) or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError as exc:
                # A tool call whose arguments do not parse is unusable. Surfaced
                # rather than guessed at.
                raise LLMMalformed(
                    f"tool call {tc.function.name} had unparseable arguments: {exc}"
                ) from exc
            if not isinstance(args, dict):
                raise LLMMalformed(f"tool call {tc.function.name} arguments were not an object")
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        usage = getattr(resp, "usage", None)
        return LLMTurn(
            text=msg.content,
            tool_calls=calls,
            finish_reason=getattr(choice, "finish_reason", "stop") or "stop",
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            raw_message=_assistant_message(msg, calls),
        )


def _assistant_message(msg, calls: list[ToolCall]) -> dict:
    """Rebuild the assistant turn for the next request.

    Tool-call ids must survive verbatim -- the tool results are matched to them
    by id, and a regenerated id silently breaks the conversation.
    """
    out: dict = {"role": "assistant", "content": msg.content or ""}
    if calls:
        out["tool_calls"] = [
            {"id": c.id, "type": "function",
             "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
            for c in calls
        ]
    return out


def salvage_json(text: str | None) -> dict | None:
    """Recover a JSON object emitted as prose.

    `gpt-oss-120b` sometimes answers a structured request in text rather than
    through the tool. This finds an object if there is one; it never fabricates
    fields, and the caller still validates everything against the closed
    vocabulary before anything reaches the policy engine.
    """
    if not text:
        return None
    m = _JSON_BLOCK.search(text)
    if not m:
        return None
    try:
        value = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _classify(exc: Exception) -> Exception:
    """Map provider exceptions onto Munshi's error classes."""
    name = type(exc).__name__
    text = str(exc).lower()
    status = getattr(exc, "status_code", None)

    if name in ("AuthenticationError", "PermissionDeniedError") or status in (401, 403):
        return LLMUnavailable(f"groq rejected the credential: {exc}")
    if name == "RateLimitError" or status == 429 or "rate limit" in text:
        return LLMRateLimited(str(exc))
    if name in ("APITimeoutError", "APIConnectionError") or "timeout" in text or "timed out" in text:
        return LLMTimeout(str(exc))
    if name == "APIConnectionError" or "connection" in text:
        return LLMUnavailable(str(exc))
    if status and 500 <= int(status) < 600:
        return LLMUnavailable(f"groq server error {status}: {exc}")
    if name in ("BadRequestError", "UnprocessableEntityError") or status == 400:
        return LLMMalformed(f"groq rejected the request: {exc}")
    return LLMUnavailable(f"{name}: {exc}")
