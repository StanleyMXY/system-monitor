from unittest.mock import MagicMock
from monitor.account_monitor import check_frozen_balance


def _make_conn(rows):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    cur.fetchall.side_effect = rows
    return conn


THRESHOLDS = {
    "frozen_balance": {
        "growth_rate_warning": 0.50,
        "check_interval_minutes": 60,
    }
}


def test_check_frozen_balance_returns_one_metric():
    conn = _make_conn([
        [{"current_frozen": 12000.0}],
        [{"prev_frozen": 10000.0}],
    ])
    results = check_frozen_balance(conn, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "frozen_balance_growth_rate"


def test_check_frozen_balance_calculates_growth_rate():
    conn = _make_conn([
        [{"current_frozen": 15000.0}],
        [{"prev_frozen": 10000.0}],
    ])
    results = check_frozen_balance(conn, THRESHOLDS)
    assert abs(results[0].value - 0.5) < 1e-9


def test_check_frozen_balance_zero_previous():
    conn = _make_conn([
        [{"current_frozen": 5000.0}],
        [{"prev_frozen": 0.0}],
    ])
    results = check_frozen_balance(conn, THRESHOLDS)
    assert results[0].value == 0.0


def test_check_frozen_balance_extra_has_amounts():
    conn = _make_conn([
        [{"current_frozen": 12000.0}],
        [{"prev_frozen": 10000.0}],
    ])
    results = check_frozen_balance(conn, THRESHOLDS)
    assert results[0].extra["current_amount"] == 12000.0
    assert results[0].extra["previous_amount"] == 10000.0
