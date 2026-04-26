import time
import pytest
from unittest.mock import MagicMock, patch
from executor.action_executor import enqueue_action


def make_mock_conn():
    cursor = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn, cursor


def test_enqueue_action_inserts_row():
    conn, cursor = make_mock_conn()

    with patch("executor.action_executor.get_monitor_conn", return_value=conn):
        enqueue_action(
            domain="payment",
            action_type="switch_channel",
            target_id="ch_001",
            payload={"from": "ch_001", "to": "ch_002"},
            priority=1,
            triggered_by="recharge_success_rate",
            metric_value=0.55,
            threshold=0.60,
        )

    cursor.execute.assert_called_once()
    sql, args = cursor.execute.call_args
    assert "INSERT INTO monitor_action_queue" in sql[0]
    assert sql[1][0] == "payment"
    assert sql[1][1] == "switch_channel"
    assert sql[1][2] == "ch_001"
    conn.commit.assert_called_once()
    conn.close.assert_called_once()


def test_enqueue_action_target_id_none():
    conn, cursor = make_mock_conn()

    with patch("executor.action_executor.get_monitor_conn", return_value=conn):
        enqueue_action(
            domain="payment",
            action_type="alert",
            target_id=None,
            payload={"note": "test"},
            priority=2,
            triggered_by="withdraw_queue_count",
            metric_value=60.0,
            threshold=50.0,
        )

    _, args = cursor.execute.call_args
    assert cursor.execute.call_args.args[1][2] is None
