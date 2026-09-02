"""The regulatory envelope. These are the rules that must hold at 02:00."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from munshi import compliance as c
from munshi.clock import local
from munshi.policy import PolicyEngine
from tests.conftest import make_case
from tests.test_policy_safety import ctx_for, plan

TZ = "Asia/Kolkata"


def ts(hour, minute=0, day=24):
    return int(datetime(2026, 8, day, hour, minute, tzinfo=ZoneInfo(TZ)).timestamp())


@pytest.mark.parametrize("hour,ok", [(2, False), (7, False), (8, True), (12, True),
                                     (18, True), (19, False), (22, False), (23, False)])
def test_rbi_contact_window(hour, ok):
    assert c.contact_window_ok(ts(hour), TZ)[0] is ok


def test_out_of_window_contact_defers_to_the_next_morning(conn):
    """An automated message at 22:14 is a Fair Practices Code violation, not a
    lesser offence than a phone call. The case must be deferred, not dropped."""
    case = make_case(conn, opened_at=ts(21))
    now = ts(22, 14)
    eng = PolicyEngine(conn)
    eng.begin_run()
    d = eng.evaluate(case, plan("send_recovery_link"), ctx_for(conn, case, now), now)
    assert d.decision == "deny"
    assert d.stop_reason is None, "this is a deferral, not a write-off"
    assert local(d.reschedule_at, TZ).hour == 8
    assert d.reschedule_at > now


@pytest.mark.parametrize("hour,minute,ok", [(9, 0, True), (11, 30, False), (14, 0, True),
                                            (18, 0, False), (22, 0, True), (21, 0, False)])
def test_npci_non_peak_debit_windows(hour, minute, ok):
    assert c.npci_debit_window_ok(ts(hour, minute), TZ)[0] is ok


def test_next_debit_window_is_always_in_the_future_or_now():
    for hour in range(24):
        t = ts(hour)
        assert c.next_debit_window(t, TZ) >= t


def test_emandate_above_afa_ceiling_cannot_be_presented_automatically():
    ok, note = c.mandate_debit_ok(20_000 * 100, ts(1), ts(23))
    assert not ok and "AFA-free" in note


def test_elevated_categories_get_the_higher_ceiling():
    assert c.mandate_debit_ok(50_000 * 100, ts(1, day=23), ts(23), "mutual_fund")[0]
    assert not c.mandate_debit_ok(50_000 * 100, ts(1, day=23), ts(23), "streaming")[0]


def test_pre_debit_notification_must_be_at_least_24h_ahead():
    assert not c.mandate_debit_ok(5_000 * 100, None, ts(23))[0]
    assert not c.mandate_debit_ok(5_000 * 100, ts(10), ts(23))[0]        # 13h
    assert c.mandate_debit_ok(5_000 * 100, ts(10, day=23), ts(20))[0]    # 34h


def test_mandate_debit_is_blocked_without_notice_even_for_a_tiny_amount(conn):
    case = make_case(conn, method="emandate", amount_paise=9900,
                     error_reason="insufficient_funds")
    now = ts(9)  # inside the NPCI window, so only the notice rule can bite
    eng = PolicyEngine(conn)
    eng.begin_run()
    d = eng.evaluate(case, plan("retry_payment", channel=None, message=None),
                     ctx_for(conn, case, now), now)
    assert d.decision == "deny"
    assert "emandate_pre_debit_notice" in d.blocked_by
