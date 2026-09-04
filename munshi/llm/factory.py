"""Provider selection.

Groq is used when a key is present. Otherwise the caller is told plainly, rather
than being silently handed a stand-in that would make the run look like a model
produced it.
"""

from __future__ import annotations

from ..config import settings
from .base import LLMProvider, LLMUnavailable


def build_provider(force: str | None = None) -> LLMProvider:
    """`force` is 'groq' or 'mock'; otherwise chosen from configuration."""
    choice = force or settings().llm_provider

    if choice == "mock":
        from .mock_provider import MockProvider

        return MockProvider()

    from .groq_provider import GroqProvider

    return GroqProvider()


def provider_available(force: str | None = None) -> bool:
    try:
        build_provider(force)
    except LLMUnavailable:
        return False
    return True
