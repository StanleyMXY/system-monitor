import pytest
from engine.models import MetricResult
from engine.rule_engine import evaluate


PAYMENT_THRESHOLDS = {
    "recharge": {
        "success_rate_warning": 0.80,
        "success_rate_critical": 0.60,
        "timeout_rate_warning": 0.10,
        "pending_count_warning": 20,
    },
    "withdraw": {
        "queue_count_warning": 50,
        "fail_rate_warning": 0.20,
    },
    "channel_account": {
        "balance_warning_amount": 10000,
    },
}


def make_metric(metric, value, channel_id=None, extra=None):
    return MetricResult(
        domain="payment",
        metric=metric,
        value=value,
        channel_id=channel_id,
        extra=extra or {},
    )


def test_recharge_success_rate_ok():
    results = evaluate([make_metric("recharge_success_rate", 0.95)], PAYMENT_THRESHOLDS)
    assert len(results) == 1
    assert results[0].level == "ok"
    assert results[0].action == "none"


def test_recharge_success_rate_warning():
    results = evaluate([make_metric("recharge_success_rate", 0.75)], PAYMENT_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"
    assert results[0].threshold == 0.80


def test_recharge_success_rate_critical():
    results = evaluate([make_metric("recharge_success_rate", 0.55)], PAYMENT_THRESHOLDS)
    assert results[0].level == "critical"
    assert results[0].action == "enqueue"
    assert results[0].threshold == 0.60


def test_recharge_timeout_rate_warning():
    results = evaluate([make_metric("recharge_timeout_rate", 0.15)], PAYMENT_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"


def test_recharge_timeout_rate_ok():
    results = evaluate([make_metric("recharge_timeout_rate", 0.05)], PAYMENT_THRESHOLDS)
    assert results[0].level == "ok"


def test_recharge_pending_count_warning():
    results = evaluate([make_metric("recharge_pending_count", 25)], PAYMENT_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"


def test_channel_balance_warning():
    results = evaluate([make_metric("channel_balance", 5000)], PAYMENT_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"
    assert results[0].threshold == 10000


def test_withdraw_queue_count_warning():
    results = evaluate([make_metric("withdraw_queue_count", 60)], PAYMENT_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"


def test_withdraw_fail_rate_ok():
    results = evaluate([make_metric("withdraw_fail_rate", 0.10)], PAYMENT_THRESHOLDS)
    assert results[0].level == "ok"


def test_withdraw_fail_rate_warning():
    results = evaluate([make_metric("withdraw_fail_rate", 0.25)], PAYMENT_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"


def test_unknown_metric_returns_ok():
    results = evaluate([make_metric("unknown_metric", 999)], PAYMENT_THRESHOLDS)
    assert results[0].level == "ok"
    assert results[0].action == "none"


def test_multiple_metrics():
    metrics = [
        make_metric("recharge_success_rate", 0.55),
        make_metric("channel_balance", 5000),
    ]
    results = evaluate(metrics, PAYMENT_THRESHOLDS)
    assert len(results) == 2
    levels = {r.metric.metric: r.level for r in results}
    assert levels["recharge_success_rate"] == "critical"
    assert levels["channel_balance"] == "warning"
