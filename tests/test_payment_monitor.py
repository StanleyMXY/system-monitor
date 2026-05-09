import time
import pytest
from unittest.mock import MagicMock, patch
from monitor.payment_monitor import (
    check_recharge,
    check_channel_balance,
    check_withdraw_queue,
    check_withdraw_fail_rate,
)

THRESHOLDS = {
    "recharge": {
        "pending_timeout_minutes": 30,
        "pending_count_warning": 20,
    },
    "withdraw": {
        "queue_timeout_hours": 4,
        "fail_rate_warning": 0.20,
    },
    "channel_account": {
        "balance_warning_amount": 10000,
    },
}


def make_cursor(rows):
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    return cursor


def make_conn(rows):
    conn = MagicMock()
    conn.cursor.return_value = make_cursor(rows)
    return conn


def test_check_recharge_returns_metrics_per_channel():
    rows = [
        {"channel_id": 1, "channel_name": "渠道A", "total": 100, "success": 85, "timeout": 5, "pending_overdue": 3},
        {"channel_id": 2, "channel_name": "渠道B", "total": 50,  "success": 25, "timeout": 2, "pending_overdue": 0},
    ]
    conn = make_conn(rows)
    results = check_recharge(conn, THRESHOLDS)

    assert len(results) == 6  # 每渠道 3 条: success_rate / timeout_rate / pending_count
    metrics = {(r.channel_id, r.metric) for r in results}
    assert (1, "recharge_success_rate") in metrics
    assert (1, "recharge_timeout_rate") in metrics
    assert (1, "recharge_pending_count") in metrics
    assert (2, "recharge_success_rate") in metrics


def test_check_recharge_success_rate_calculation():
    rows = [{"channel_id": 1, "channel_name": "A", "total": 100, "success": 80, "timeout": 0, "pending_overdue": 0}]
    conn = make_conn(rows)
    results = check_recharge(conn, THRESHOLDS)
    sr = next(r for r in results if r.metric == "recharge_success_rate")
    assert sr.value == pytest.approx(0.80)


def test_check_recharge_empty_rows_triggers_no_data_alert():
    conn = make_conn([])
    results = check_recharge(conn, THRESHOLDS)
    assert len(results) == 1
    r = results[0]
    assert r.domain == "payment"
    assert r.metric == "recharge_success_rate"
    assert r.value == 0.0
    assert r.channel_id is None
    assert r.extra["order_count"] == 0


def test_check_channel_balance_returns_one_per_account():
    rows = [
        {"account_id": 10, "account_name": "账户A", "balance": 5000.0},
        {"account_id": 11, "account_name": "账户B", "balance": 20000.0},
    ]
    conn = make_conn(rows)
    results = check_channel_balance(conn, THRESHOLDS)
    assert len(results) == 2
    assert all(r.metric == "channel_balance" for r in results)
    values = {r.channel_id: r.value for r in results}
    assert values[10] == 5000.0


def test_check_withdraw_queue_returns_single_metric():
    rows = [{"overdue_count": 60}]
    conn = make_conn(rows)
    results = check_withdraw_queue(conn, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "withdraw_queue_count"
    assert results[0].value == 60


def test_check_withdraw_fail_rate_with_data():
    rows = [{"total_processed": 100, "failed": 25}]
    conn = make_conn(rows)
    results = check_withdraw_fail_rate(conn, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "withdraw_fail_rate"
    assert results[0].value == pytest.approx(0.25)


def test_check_withdraw_fail_rate_no_data():
    rows = [{"total_processed": 0, "failed": 0}]
    conn = make_conn(rows)
    results = check_withdraw_fail_rate(conn, THRESHOLDS)
    assert results == []
