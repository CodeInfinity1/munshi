"""The taxonomy is the product's load-bearing claim. These pin it down."""

from munshi import taxonomy
from munshi.taxonomy import UNRETRYABLE_ON_SAME_INSTRUMENT, lookup


def test_every_reason_resolves_to_a_known_family():
    families = set(taxonomy.families())
    for reason in taxonomy.all_reasons():
        assert lookup(reason).family in families


def test_expired_cards_are_never_retryable():
    # If this ever flips, the product's headline claim is false.
    for reason in ("card_expired", "debit_instrument_blocked", "card_number_invalid",
                   "invalid_vpa", "bank_account_invalid", "transaction_on_vpa_restricted"):
        assert lookup(reason).retry_is_futile, reason


def test_timing_dependent_failures_stay_retryable():
    for reason in ("insufficient_funds", "bank_technical_error", "gateway_technical_error",
                   "transaction_daily_limit_exceeded", "issuer_technical_error"):
        assert not lookup(reason).retry_is_futile, reason


def test_risk_declines_are_never_retryable():
    assert lookup("payment_risk_check_failed").retryability == taxonomy.NEVER
    assert lookup("compliance_violation").retryability == taxonomy.NEVER


def test_merchant_side_failures_do_not_target_the_customer():
    for reason in ("payment_method_not_enabled", "bank_not_enabled",
                   "recurring_payment_not_enabled", "invalid_order_id",
                   "order_amount_mismatch", "order_already_paid"):
        assert not lookup(reason).contacts_customer, reason


def test_unknown_reason_degrades_conservatively():
    """An unrecognised code must never widen what the agent may do."""
    u = lookup("some_code_razorpay_added_last_week")
    assert u.reason == "unknown"
    assert u.min_backoff_hours >= 12
    assert u.retryability not in UNRETRYABLE_ON_SAME_INSTRUMENT  # not silently written off


def test_backoff_floors_are_present_where_timing_matters():
    assert lookup("insufficient_funds").min_backoff_hours == 24
    assert lookup("transaction_daily_limit_exceeded").min_backoff_hours == 24
    assert lookup("payment_cancelled").min_backoff_hours == 0
