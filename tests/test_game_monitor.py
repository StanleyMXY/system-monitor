import pytest
from unittest.mock import MagicMock
from monitor.game_monitor import check_balance_transfer, check_reconciliation

THRESHOLDS = {
    "balance_transfer": {
        "retry_count_warning": 3,
        "retry_order_count_warning": 5,
    },
    "reconciliation": {
        "diff_consecutive_days_critical": 2,
        "diff_amount_warning": 100,
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


def test_check_balance_transfer_returns_metrics_per_vendor():
    rows = [
        {"vendor_id": "HG_JILI", "provider_code": "JILI", "total": 100, "failed": 3, "retry_anomaly_count": 2},
        {"vendor_id": "HG_PG", "provider_code": "PG", "total": 50, "failed": 5, "retry_anomaly_count": 6},
    ]
    conn = make_conn(rows)
    results = check_balance_transfer(conn, THRESHOLDS)

    assert len(results) == 4  # 每供应商 2 条: fail_rate + retry_count
    metrics = {(r.extra["manufacturer_name"], r.metric) for r in results}
    assert ("JILI", "game_transfer_fail_rate") in metrics
    assert ("JILI", "game_transfer_retry_count") in metrics
    assert ("PG", "game_transfer_fail_rate") in metrics
    assert ("PG", "game_transfer_retry_count") in metrics


def test_check_balance_transfer_fail_rate_calculation():
    rows = [{"vendor_id": "HG_JILI", "provider_code": "JILI", "total": 100, "failed": 5, "retry_anomaly_count": 0}]
    conn = make_conn(rows)
    results = check_balance_transfer(conn, THRESHOLDS)
    rate = next(r for r in results if r.metric == "game_transfer_fail_rate")
    assert rate.value == pytest.approx(0.05)


def test_check_balance_transfer_empty():
    conn = make_conn([])
    results = check_balance_transfer(conn, THRESHOLDS)
    assert results == []


def test_check_balance_transfer_total_zero_skipped():
    rows = [{"vendor_id": "HG_JILI", "provider_code": "JILI", "total": 0, "failed": 0, "retry_anomaly_count": 0}]
    conn = make_conn(rows)
    results = check_balance_transfer(conn, THRESHOLDS)
    assert results == []


def test_check_reconciliation_returns_diff_days_per_vendor():
    rows = [
        {"manuf": "JILI", "consecutive_diff_days": 3},
        {"manuf": "PG", "consecutive_diff_days": 0},
    ]
    conn = make_conn(rows)
    results = check_reconciliation(conn, THRESHOLDS)

    assert len(results) == 2
    assert all(r.metric == "game_reconciliation_diff_days" for r in results)
    vals = {r.extra["manufacturer_name"]: r.value for r in results}
    assert vals["JILI"] == 3.0
    assert vals["PG"] == 0.0
