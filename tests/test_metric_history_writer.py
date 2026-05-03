import time
import pytest
from unittest.mock import MagicMock, patch
from engine.models import MetricResult, RuleResult


def make_rule_result(domain, metric, value, level, channel_id=None):
    m = MetricResult(domain=domain, metric=metric, value=value, channel_id=channel_id)
    return RuleResult(level=level, action="none", metric=m, threshold=0.0, message="")


def test_write_metric_history_inserts_all_results():
    results = [
        make_rule_result("payment", "recharge_success_rate", 0.92, "ok"),
        make_rule_result("payment", "channel_balance", 5000.0, "warning", channel_id=3),
    ]

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    with patch("scheduler.get_monitor_conn", return_value=mock_conn):
        from scheduler import _write_metric_history
        _write_metric_history(results)

    assert mock_cursor.executemany.called
    args = mock_cursor.executemany.call_args
    rows = args[0][1]
    assert len(rows) == 2
    assert rows[0][1] == "recharge_success_rate"
    assert rows[0][3] == 0.92
    assert rows[0][4] == "ok"
    assert rows[1][2] == 3


def test_write_metric_history_raises_on_db_failure():
    """_write_metric_history 遇到 DB 异常时应向上抛，由调用方捕获。"""
    results = [make_rule_result("payment", "recharge_success_rate", 0.92, "ok")]

    with patch("scheduler.get_monitor_conn", side_effect=Exception("DB down")):
        from scheduler import _write_metric_history
        with pytest.raises(Exception, match="DB down"):
            _write_metric_history(results)
