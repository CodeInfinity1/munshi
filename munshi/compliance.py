"""The regulatory envelope Munshi is not allowed to step outside.

Indian payments recovery is not a free-form optimisation problem. Three published
rule sets bound *when* and *how* an automated system may chase money, and they
are encoded here as deterministic checks rather than left to a model's judgement:

1. **RBI Fair Practices Code -- recovery contact hours.** A borrower may only be
   contacted between 08:00 and 19:00 local time. This covers calls, SMS,
   WhatsApp and email; an automated message at midnight is a violation in its own
   right, not a lesser offence than a phone call.

2. **RBI Digital Payments E-mandate Framework (2026) -- pre-debit notification.**
   A pre-debit notification must reach the payer at least 24 hours before each
   scheduled debit, and recurring debits above the AFA-free ceiling
   (Rs 15,000 in general; Rs 1,00,000 for mutual funds, insurance premiums and
   credit-card bill payments) require fresh additional-factor authentication.

3. **NPCI auto-debit processing windows.** Mandate debits are processed in
   non-peak windows -- before 10:00, 13:00-17:00, and after 21:30. Presenting a
   mandate outside those windows collides with peak congestion and depresses the
   success rate.

These are implemented from published guidance and exposed as configuration, not
asserted as legal advice. The point for the agent is structural: a compliance
rule is a hard bound the reasoning layer cannot argue its way past.
"""

from __future__ import annotations

from datetime import timedelta

from .clock import local

# --- RBI Fair Practices Code -------------------------------------------------
CONTACT_WINDOW = (8, 19)  # [08:00, 19:00) local time

# --- RBI e-mandate framework -------------------------------------------------
PRE_DEBIT_NOTICE_HOURS = 24
AFA_FREE_LIMIT_PAISE = 15_000 * 100
AFA_FREE_LIMIT_ELEVATED_PAISE = 1_00_000 * 100
ELEVATED_CATEGORIES = frozenset({"mutual_fund", "insurance", "credit_card_bill"})

# --- NPCI auto-debit non-peak windows ---------------------------------------
NPCI_DEBIT_WINDOWS = ((0, 10), (13, 17), (21.5, 24))


def contact_window_ok(ts: int, tz: str) -> tuple[bool, str]:
    """RBI FPC: may we reach this customer at this instant?"""
    hour = local(ts, tz).hour
    lo, hi = CONTACT_WINDOW
    if lo <= hour < hi:
        return True, f"{hour:02d}:xx local is inside the RBI FPC contact window {lo:02d}:00-{hi:02d}:00"
    return False, (
        f"{hour:02d}:xx local is outside the RBI Fair Practices Code contact window "
        f"{lo:02d}:00-{hi:02d}:00; automated messages count as contact"
    )


def next_contact_time(ts: int, tz: str) -> int:
    """Earliest instant at or after `ts` at which contact is permitted."""
    lo, hi = CONTACT_WINDOW
    dt = local(ts, tz)
    if lo <= dt.hour < hi:
        return ts
    target = dt.replace(hour=lo, minute=0, second=0, microsecond=0)
    if dt.hour >= hi:
        target += timedelta(days=1)
    return int(target.timestamp())


def npci_debit_window_ok(ts: int, tz: str) -> tuple[bool, str]:
    """NPCI: is this instant inside a non-peak auto-debit processing window?"""
    dt = local(ts, tz)
    h = dt.hour + dt.minute / 60
    for lo, hi in NPCI_DEBIT_WINDOWS:
        if lo <= h < hi:
            return True, f"{dt.hour:02d}:{dt.minute:02d} is inside NPCI non-peak window {lo}-{hi}"
    return False, (
        f"{dt.hour:02d}:{dt.minute:02d} local falls in an NPCI peak window; mandate "
        "presentation here contends with congestion and depresses success rates"
    )


def next_debit_window(ts: int, tz: str) -> int:
    """Earliest instant at or after `ts` inside an NPCI non-peak window."""
    dt = local(ts, tz)
    for _ in range(48):
        h = dt.hour + dt.minute / 60
        if any(lo <= h < hi for lo, hi in NPCI_DEBIT_WINDOWS):
            return int(dt.timestamp())
        dt += timedelta(minutes=30)
    return ts


def mandate_debit_ok(
    amount_paise: int, notified_at: int | None, debit_at: int, category: str | None = None
) -> tuple[bool, str]:
    """RBI e-mandate: may this recurring debit be presented autonomously?"""
    ceiling = (
        AFA_FREE_LIMIT_ELEVATED_PAISE if category in ELEVATED_CATEGORIES else AFA_FREE_LIMIT_PAISE
    )
    if amount_paise > ceiling:
        return False, (
            f"Rs {amount_paise / 100:,.0f} exceeds the AFA-free e-mandate ceiling of "
            f"Rs {ceiling / 100:,.0f}; fresh additional-factor authentication is required, "
            "which only the customer can complete"
        )
    if notified_at is None:
        return False, (
            "no pre-debit notification on record; the RBI e-mandate framework requires one "
            f"at least {PRE_DEBIT_NOTICE_HOURS}h before the debit"
        )
    gap_h = (debit_at - notified_at) / 3600
    if gap_h < PRE_DEBIT_NOTICE_HOURS:
        return False, (
            f"pre-debit notification was only {gap_h:.1f}h ahead of the debit; the framework "
            f"requires at least {PRE_DEBIT_NOTICE_HOURS}h"
        )
    return True, f"pre-debit notification sent {gap_h:.0f}h ahead; amount within the AFA-free ceiling"
