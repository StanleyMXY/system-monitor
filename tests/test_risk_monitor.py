import time
import pytest
from unittest.mock import MagicMock
from monitor.risk_monitor import (
    check_alert_backlog,
    check_alert_timeout,
    check_event_backlog,
    check_blacklist_expiry,
)

THRESHOLDS = {
    "alert": {
        "high_risk_pending_warning": 10,
        "high_risk_timeout_hours": 2,
    },
    "event": {
        "high_risk_pending_warning": 5,
    },
    "blacklist": {
        "expiry_reminder_hours": 24,
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


def test_check_alert_backlog_returns_single_metric():
    rows = [{"backlog_count": 15}]
    conn = make_conn(rows)
    results = check_alert_backlog(conn, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "risk_alert_backlog_count"
    assert results[0].value == 15.0


def test_check_alert_timeout_returns_single_metric():
    rows = [{"timeout_count": 3}]
    conn = make_conn(rows)
    results = check_alert_timeout(conn, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "risk_alert_timeout_count"
    assert results[0].value == 3.0


def test_check_event_backlog_returns_single_metric():
    rows = [{"backlog_count": 7}]
    conn = make_conn(rows)
    results = check_event_backlog(conn, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "risk_event_backlog_count"
    assert results[0].value == 7.0


def test_check_blacklist_expiry_returns_single_metric():
    rows = [{"expiry_count": 2}]
    conn = make_conn(rows)
    results = check_blacklist_expiry(conn, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "risk_blacklist_expiry_count"
    assert results[0].value == 2.0
