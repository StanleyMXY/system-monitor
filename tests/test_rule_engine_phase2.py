import pytest
from engine.models import MetricResult
from engine.rule_engine import evaluate

GAME_THRESHOLDS = {
    "balance_transfer": {
        "fail_rate_warning": 0.05,
        "retry_order_count_warning": 5,
    },
    "reconciliation": {
        "diff_consecutive_days_critical": 2,
    },
}

RISK_THRESHOLDS = {
    "alert": {
        "high_risk_pending_warning": 10,
    },
    "event": {
        "high_risk_pending_warning": 5,
    },
}


def make_metric(metric, value, domain="game", channel_id=None):
    return MetricResult(domain=domain, metric=metric, value=value, channel_id=channel_id)


# --- game rules ---

def test_game_transfer_fail_rate_ok():
    results = evaluate([make_metric("game_transfer_fail_rate", 0.03)], GAME_THRESHOLDS)
    assert results[0].level == "ok"


def test_game_transfer_fail_rate_warning():
    results = evaluate([make_metric("game_transfer_fail_rate", 0.08)], GAME_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"
    assert results[0].threshold == 0.05


def test_game_transfer_retry_count_ok():
    results = evaluate([make_metric("game_transfer_retry_count", 3.0)], GAME_THRESHOLDS)
    assert results[0].level == "ok"


def test_game_transfer_retry_count_warning():
    results = evaluate([make_metric("game_transfer_retry_count", 6.0)], GAME_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"


def test_game_reconciliation_ok():
    results = evaluate([make_metric("game_reconciliation_diff_days", 1.0)], GAME_THRESHOLDS)
    assert results[0].level == "ok"


def test_game_reconciliation_critical():
    results = evaluate([make_metric("game_reconciliation_diff_days", 3.0)], GAME_THRESHOLDS)
    assert results[0].level == "critical"
    assert results[0].action == "enqueue"
    assert results[0].threshold == 2


# --- risk rules ---

def test_risk_alert_backlog_ok():
    results = evaluate([make_metric("risk_alert_backlog_count", 5.0, domain="risk")], RISK_THRESHOLDS)
    assert results[0].level == "ok"


def test_risk_alert_backlog_warning():
    results = evaluate([make_metric("risk_alert_backlog_count", 15.0, domain="risk")], RISK_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"


def test_risk_alert_timeout_zero_ok():
    results = evaluate([make_metric("risk_alert_timeout_count", 0.0, domain="risk")], RISK_THRESHOLDS)
    assert results[0].level == "ok"


def test_risk_alert_timeout_nonzero_warning():
    results = evaluate([make_metric("risk_alert_timeout_count", 1.0, domain="risk")], RISK_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"


def test_risk_event_backlog_warning():
    results = evaluate([make_metric("risk_event_backlog_count", 8.0, domain="risk")], RISK_THRESHOLDS)
    assert results[0].level == "warning"


def test_risk_blacklist_expiry_zero_ok():
    results = evaluate([make_metric("risk_blacklist_expiry_count", 0.0, domain="risk")], RISK_THRESHOLDS)
    assert results[0].level == "ok"


def test_risk_blacklist_expiry_nonzero_warning():
    results = evaluate([make_metric("risk_blacklist_expiry_count", 2.0, domain="risk")], RISK_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"
