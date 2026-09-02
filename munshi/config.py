"""Runtime configuration. Everything sensitive comes from the environment."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

try:  # optional convenience only; the app works fine without a .env file
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw and raw.strip() else default


def _env(name: str, default: str | None = None):
    """Read at construction time, not at class-definition time.

    Plain dataclass defaults are evaluated once when the module is imported, which
    silently froze configuration before a .env file or a test fixture could change
    it. Every field here goes through a factory instead.
    """
    v = os.getenv(name)
    return v if v not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    db_path: Path = field(default_factory=lambda: Path(_env("MUNSHI_DB", "munshi.db")))
    timezone: str = field(default_factory=lambda: _env("MUNSHI_TIMEZONE", "Asia/Kolkata"))

    # --- reasoning -----------------------------------------------------------
    anthropic_api_key: str | None = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("MUNSHI_LLM_MODEL", "claude-opus-5"))
    llm_effort: str = field(default_factory=lambda: _env("MUNSHI_LLM_EFFORT", "low"))
    llm_concurrency: int = field(default_factory=lambda: _int("MUNSHI_LLM_CONCURRENCY", 8))

    # --- execution -----------------------------------------------------------
    adapter: str = field(default_factory=lambda: _env("MUNSHI_ADAPTER", "simulator"))
    razorpay_key_id: str | None = field(default_factory=lambda: _env("RAZORPAY_KEY_ID"))
    razorpay_key_secret: str | None = field(default_factory=lambda: _env("RAZORPAY_KEY_SECRET"))
    razorpay_webhook_secret: str | None = field(
        default_factory=lambda: _env("RAZORPAY_WEBHOOK_SECRET"))

    # --- api -----------------------------------------------------------------
    api_token: str = field(
        default_factory=lambda: _env("MUNSHI_API_TOKEN") or secrets.token_urlsafe(24))
    rate_limit_per_minute: int = field(default_factory=lambda: _int("MUNSHI_RATE_LIMIT", 120))

    @property
    def llm_available(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def razorpay_credentials_present(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    def describe(self) -> dict[str, object]:
        """Non-secret summary surfaced in the UI so the demo can never overclaim."""
        return {
            "reasoner": "llm" if self.llm_available else "heuristic",
            "llm_model": self.llm_model if self.llm_available else None,
            "llm_effort": self.llm_effort if self.llm_available else None,
            "adapter": self.effective_adapter,
            "razorpay_credentials_present": self.razorpay_credentials_present,
            "timezone": self.timezone,
        }

    @property
    def effective_adapter(self) -> str:
        """Never silently issue live API calls: razorpay_test needs explicit opt-in AND keys."""
        if self.adapter == "razorpay_test" and self.razorpay_credentials_present:
            return "razorpay_test"
        return "simulator"


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
