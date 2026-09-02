"""Razorpay payment-failure taxonomy, loaded once and exposed as recovery semantics.

This module is the reason Munshi can be *selective*. A generic dunning tool retries
every failed payment on a fixed ladder. Razorpay publishes, for every failure, an
`error_source` (customer / business / gateway / razorpay) and an `error_reason` from a
closed vocabulary -- and for each reason, documented guidance on who must act. That
turns "should we retry?" from a guess into a lookup.

Nothing in here calls an LLM. Retryability is a property of the failure code, so it
is resolved deterministically and the model never gets a vote on it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).parent / "data" / "razorpay_failure_taxonomy.json"

# A retry can only ever succeed if the precondition it depends on can change.
NEVER = "never"
NEW_INSTRUMENT_ONLY = "new_instrument_only"
AFTER_STATE_CHANGE = "after_state_change"
IMMEDIATE = "immediate"

#: Retryability classes for which re-attempting the *same* instrument is a
#: guaranteed-zero action. The policy engine hard-blocks retries on these.
UNRETRYABLE_ON_SAME_INSTRUMENT = frozenset({NEVER, NEW_INSTRUMENT_ONLY})


@dataclass(frozen=True, slots=True)
class FailureSemantics:
    """Everything Munshi knows about one Razorpay failure reason."""

    reason: str
    family: str
    family_label: str
    retryability: str
    blame: str
    default_intervention: str
    rationale: str
    description: str
    next_step: str
    resolution_requires: str
    sources: tuple[str, ...]
    min_backoff_hours: float | None

    @property
    def retry_is_futile(self) -> bool:
        """True when re-attempting the same instrument cannot possibly succeed."""
        return self.retryability in UNRETRYABLE_ON_SAME_INSTRUMENT

    @property
    def contacts_customer(self) -> bool:
        """False when the fix is on the merchant's side; contacting the payer is wrong."""
        return self.blame not in {"merchant", "engineering", "none"}


@lru_cache(maxsize=1)
def _raw() -> dict:
    return json.loads(_DATA.read_text())


@lru_cache(maxsize=1)
def _table() -> dict[str, FailureSemantics]:
    raw = _raw()
    families = raw["families"]
    out: dict[str, FailureSemantics] = {}
    for reason, r in raw["reasons"].items():
        fam = families[r["family"]]
        out[reason] = FailureSemantics(
            reason=reason,
            family=r["family"],
            family_label=fam["label"],
            retryability=fam["retryability"],
            blame=fam["blame"],
            default_intervention=fam["default_intervention"],
            rationale=fam["rationale"],
            description=r["description"],
            next_step=r["next_step"],
            resolution_requires=r["resolution_requires"],
            sources=tuple(r["sources"]),
            min_backoff_hours=r["min_backoff_hours"],
        )
    return out


#: Fallback used when Razorpay hands us a reason code we have not classified.
#: Deliberately conservative: treat the unknown as opaque-but-possibly-transient,
#: with a long backoff, rather than assuming it is safe to hammer.
UNKNOWN = FailureSemantics(
    reason="unknown",
    family="transient_infra",
    family_label="Unclassified failure",
    retryability=AFTER_STATE_CHANGE,
    blame="issuer",
    default_intervention="downtime_aware_retry",
    rationale="Reason code is not in Munshi's taxonomy. Treated as opaque and transient "
    "with a conservative backoff so an unrecognised code can never widen autonomy.",
    description="Failure reason not present in the Razorpay taxonomy snapshot.",
    next_step="Review manually and extend the taxonomy.",
    resolution_requires="unknown",
    sources=("gateway",),
    min_backoff_hours=12,
)


def lookup(reason: str | None) -> FailureSemantics:
    """Resolve a Razorpay `error_reason` to its recovery semantics."""
    if not reason:
        return UNKNOWN
    return _table().get(reason, UNKNOWN)


def all_reasons() -> list[str]:
    return sorted(_table())


def families() -> dict[str, dict]:
    return _raw()["families"]


def source_semantics() -> dict[str, str]:
    """Razorpay's own description of what each `error_source` means for who acts."""
    return _raw()["_meta"]["source_semantics"]


def _self_check() -> None:
    assert len(_table()) >= 60, "taxonomy shrank unexpectedly"
    # The two decisions the whole product rests on.
    assert lookup("card_expired").retry_is_futile, "expired cards must never be retried"
    assert not lookup("insufficient_funds").retry_is_futile, "NSF is a timing problem"
    assert lookup("insufficient_funds").min_backoff_hours == 24
    assert lookup("payment_risk_check_failed").retryability == NEVER
    assert not lookup("payment_method_not_enabled").contacts_customer, (
        "merchant misconfiguration must not generate customer contact"
    )
    assert lookup("order_already_paid").family == "already_settled"
    assert lookup("wat_is_this").reason == "unknown"
    assert lookup("payment_cancelled").min_backoff_hours == 0
    print(f"taxonomy ok: {len(_table())} reasons across {len(families())} families")


if __name__ == "__main__":
    _self_check()
