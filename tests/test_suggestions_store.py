# tests/test_suggestions_store.py
import time
import pytest
from unittest.mock import patch, MagicMock
from engine.suggestions_store import insert_suggestions, load_rejected_context


def _mock_conn(fetchall_return=None):
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    if fetchall_return is not None:
        cur.fetchall.return_value = fetchall_return
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def test_insert_suggestions_calls_execute():
    conn, cur = _mock_conn()
    rows = [
        {
            "source": "threshold_optimizer",
            "analysis_date": "2026-05-08",
            "item_type": "adjust",
            "metric_key": "recharge_success_rate",
            "payload": {"metric": "recharge_success_rate"},
        }
    ]
    with patch("engine.suggestions_store.get_monitor_conn", return_value=conn):
        insert_suggestions(rows)
    assert cur.execute.called


def test_insert_suggestions_empty_is_noop():
    conn, cur = _mock_conn()
    with patch("engine.suggestions_store.get_monitor_conn", return_value=conn):
        insert_suggestions([])
    conn.cursor.assert_not_called()


def test_load_rejected_context_returns_list():
    now_ms = int(time.time() * 1000)
    row = {
        "source": "threshold_optimizer",
        "item_type": "adjust",
        "metric_key": "recharge_success_rate",
        "payload": '{"metric": "recharge_success_rate"}',
        "reviewed_at": now_ms,
    }
    conn, cur = _mock_conn(fetchall_return=[row])
    with patch("engine.suggestions_store.get_monitor_conn", return_value=conn):
        result = load_rejected_context("threshold_optimizer", 30)
    assert len(result) == 1
    assert result[0]["metric_key"] == "recharge_success_rate"
    assert isinstance(result[0]["payload"], dict)
