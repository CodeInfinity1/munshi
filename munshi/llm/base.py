"""Provider-agnostic LLM interface.

Munshi talks to exactly one abstraction. Swapping Groq for another provider, or
for the deterministic mock, changes one construction site and nothing else --
which is also what makes the agent loop testable without a network.

Errors are classified rather than passed through raw, because the recovery loop
treats them differently: a rate limit is worth one bounded retry, a malformed
tool call is not worth retrying at all, and none of them may be allowed to touch
financial state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class LLMError(RuntimeError):
    """Base for every provider failure. Always caught by the agent loop."""


class LLMUnavailable(LLMError):
    """No credential, connection refused, or the provider is down."""


class LLMRateLimited(LLMError):
    """429. Worth one bounded retry, then give up."""


class LLMTimeout(LLMError):
    """The request exceeded its deadline."""


class LLMMalformed(LLMError):
    """The model replied, but not in a shape we can use. Never retried."""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool the model may call. `parameters` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict

    def to_openai(self) -> dict:
        """OpenAI/Groq function-tool wire format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(slots=True)
class LLMTurn:
    """One assistant turn: some text, and/or some tool calls."""

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    #: Provider-native assistant message, echoed back verbatim on the next turn.
    #: Reconstructing it loses tool-call ids and breaks the conversation.
    raw_message: dict | None = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    def chat(
        self,
        messages: list[dict],
        tools: list[ToolSpec] | None = None,
        *,
        tool_choice: str = "auto",
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> LLMTurn: ...
