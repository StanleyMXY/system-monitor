from unittest.mock import MagicMock
from monitor.operation_monitor import check_vip_adjust, check_balance_adjustment, check_config_change


def _make_conn(rows):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    cur.fetchall.side_effect = rows
    return conn


THRESHOLDS = {
    "vip_adjust": {
        "batch_count_per_hour_warning": 20,
        "check_interval_minutes": 60,
    },
    "balance_adjustment": {
        "large_amount_threshold": 10000,
        "check_interval_minutes": 10,
    },
    "config_change": {
        "change_count_per_hour_warning": 10,
        "check_interval_minutes": 60,
    },
}


def test_check_vip_adjust_returns_one_metric():
    conn = _make_conn([[{"adjust_count": 5}]])
    results = check_vip_adjust(conn, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "vip_adjust_count"
    assert results[0].value == 5.0


def test_check_balance_adjustment_returns_one_metric():
    conn = _make_conn([[{"large_count": 3, "max_amount": 50000.0}]])
    results = check_balance_adjustment(conn, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "balance_adjustment_large_count"
    assert results[0].value == 3.0
    assert results[0].extra["max_amount"] == 50000.0


def test_check_balance_adjustment_zero_rows():
    conn = _make_conn([[{"large_count": 0, "max_amount": None}]])
    results = check_balance_adjustment(conn, THRESHOLDS)
    assert results[0].value == 0.0
    assert results[0].extra["max_amount"] == 0.0


def test_check_config_change_returns_one_metric():
    conn = _make_conn([[{"change_count": 7}]])
    results = check_config_change(conn, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "config_change_count"
    assert results[0].value == 7.0
