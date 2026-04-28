import pytest
from unittest.mock import MagicMock
from monitor.activity_monitor import check_redemption, check_first_deposit


def _make_conn(rows_by_sql):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    cur.fetchall.side_effect = rows_by_sql
    return conn


THRESHOLDS = {
    "redemption": {
        "fail_rate_warning": 0.05,
        "fail_count_warning": 10,
        "check_interval_minutes": 15,
    },
    "first_deposit": {
        "fail_alert_threshold": 1,
        "volume_zero_check": True,
        "check_interval_minutes": 5,
    },
}


def test_check_redemption_returns_two_metrics():
    conn = _make_conn([
        [{"total": 100, "fail": 8}],
    ])
    results = check_redemption(conn, THRESHOLDS)
    assert len(results) == 2
    metrics = {r.metric for r in results}
    assert "redemption_fail_rate" in metrics
    assert "redemption_fail_count" in metrics


def test_check_redemption_calculates_rate():
    conn = _make_conn([
        [{"total": 100, "fail": 8}],
    ])
    results = check_redemption(conn, THRESHOLDS)
    rate = next(r for r in results if r.metric == "redemption_fail_rate")
    assert abs(rate.value - 0.08) < 1e-9


def test_check_redemption_zero_total():
    conn = _make_conn([
        [{"total": 0, "fail": 0}],
    ])
    results = check_redemption(conn, THRESHOLDS)
    rate = next(r for r in results if r.metric == "redemption_fail_rate")
    assert rate.value == 0.0


def test_check_first_deposit_returns_two_metrics():
    conn = _make_conn([
        [{"fail_count": 2, "total_count": 50}],
    ])
    results = check_first_deposit(conn, THRESHOLDS)
    assert len(results) == 2
    metrics = {r.metric for r in results}
    assert "first_deposit_fail_count" in metrics
    assert "first_deposit_volume" in metrics


def test_check_first_deposit_has_is_business_hours():
    conn = _make_conn([
        [{"fail_count": 0, "total_count": 10}],
    ])
    results = check_first_deposit(conn, THRESHOLDS)
    volume = next(r for r in results if r.metric == "first_deposit_volume")
    assert "is_business_hours" in volume.extra
    assert isinstance(volume.extra["is_business_hours"], bool)
