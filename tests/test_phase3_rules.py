from engine.models import MetricResult
from engine.rule_engine import evaluate

THRESHOLDS_ACTIVITY = {
    "redemption": {"fail_rate_warning": 0.05, "fail_count_warning": 10},
    "first_deposit": {"fail_alert_threshold": 1},
}
THRESHOLDS_ACCOUNT = {
    "frozen_balance": {"growth_rate_warning": 0.50},
}
THRESHOLDS_OPERATION = {
    "vip_adjust": {"batch_count_per_hour_warning": 20},
    "balance_adjustment": {"large_amount_threshold": 10000},
    "config_change": {"change_count_per_hour_warning": 10},
}


def _metric(metric, value, domain, extra=None):
    return MetricResult(domain=domain, metric=metric, value=value, extra=extra or {})


# --- activity ---

def test_redemption_fail_rate_warning():
    m = _metric("redemption_fail_rate", 0.08, "activity")
    result = evaluate([m], THRESHOLDS_ACTIVITY)[0]
    assert result.level == "warning"
    assert result.action == "alert"


def test_redemption_fail_rate_ok():
    m = _metric("redemption_fail_rate", 0.03, "activity")
    result = evaluate([m], THRESHOLDS_ACTIVITY)[0]
    assert result.level == "ok"


def test_redemption_fail_count_warning():
    m = _metric("redemption_fail_count", 15.0, "activity")
    result = evaluate([m], THRESHOLDS_ACTIVITY)[0]
    assert result.level == "warning"
    assert result.action == "alert"


def test_first_deposit_fail_count_warning():
    m = _metric("first_deposit_fail_count", 1.0, "activity")
    result = evaluate([m], THRESHOLDS_ACTIVITY)[0]
    assert result.level == "warning"
    assert result.action == "alert"


def test_first_deposit_fail_count_ok():
    m = _metric("first_deposit_fail_count", 0.0, "activity")
    result = evaluate([m], THRESHOLDS_ACTIVITY)[0]
    assert result.level == "ok"


# --- account ---

def test_frozen_balance_growth_warning():
    m = _metric("frozen_balance_growth_rate", 0.60, "account",
                extra={"current_amount": 16000.0, "previous_amount": 10000.0})
    result = evaluate([m], THRESHOLDS_ACCOUNT)[0]
    assert result.level == "warning"
    assert result.action == "alert"


def test_frozen_balance_growth_ok():
    m = _metric("frozen_balance_growth_rate", 0.20, "account",
                extra={"current_amount": 12000.0, "previous_amount": 10000.0})
    result = evaluate([m], THRESHOLDS_ACCOUNT)[0]
    assert result.level == "ok"


# --- operation ---

def test_vip_adjust_count_warning():
    m = _metric("vip_adjust_count", 25.0, "operation")
    result = evaluate([m], THRESHOLDS_OPERATION)[0]
    assert result.level == "warning"
    assert result.action == "enqueue"


def test_vip_adjust_count_ok():
    m = _metric("vip_adjust_count", 5.0, "operation")
    result = evaluate([m], THRESHOLDS_OPERATION)[0]
    assert result.level == "ok"


def test_balance_adjustment_large_count_warning():
    m = _metric("balance_adjustment_large_count", 1.0, "operation",
                extra={"max_amount": 50000.0})
    result = evaluate([m], THRESHOLDS_OPERATION)[0]
    assert result.level == "warning"
    assert result.action == "enqueue"


def test_balance_adjustment_large_count_ok():
    m = _metric("balance_adjustment_large_count", 0.0, "operation",
                extra={"max_amount": 0.0})
    result = evaluate([m], THRESHOLDS_OPERATION)[0]
    assert result.level == "ok"


def test_config_change_count_warning():
    m = _metric("config_change_count", 12.0, "operation")
    result = evaluate([m], THRESHOLDS_OPERATION)[0]
    assert result.level == "warning"
    assert result.action == "alert"


def test_config_change_count_ok():
    m = _metric("config_change_count", 5.0, "operation")
    result = evaluate([m], THRESHOLDS_OPERATION)[0]
    assert result.level == "ok"
