import json
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_mock_conn(rows):
    """构造返回指定 rows 的 mock DB 连接（DictCursor 风格，fetchall 返回 list[dict]）。"""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchall.return_value = rows
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn


def test_load_metric_history_returns_list():
    from engine.root_cause_analyzer import _load_metric_history
    now_ms = int(time.time() * 1000)
    rows = [{"domain": "payment", "metric": "recharge_success_rate",
             "channel_id": None, "value": 0.55, "level": "critical",
             "recorded_at": now_ms - 1000}]
    mock_conn = _make_mock_conn(rows)

    with patch("engine.root_cause_analyzer.get_monitor_conn", return_value=mock_conn):
        result = _load_metric_history()

    assert len(result) == 1
    assert result[0]["domain"] == "payment"
    assert result[0]["level"] == "critical"


def test_load_alerts_log_filters_old_entries(tmp_path, monkeypatch):
    from engine.root_cause_analyzer import _load_alerts_log
    import engine.root_cause_analyzer as ra
    log_file = tmp_path / "alerts.log"
    now = int(time.time())
    old_entry = json.dumps({"ts": now - 90000, "level": "warning", "domain": "payment"})
    new_entry = json.dumps({"ts": now - 100, "level": "critical", "domain": "game"})
    log_file.write_text(f"{old_entry}\n{new_entry}\n", encoding="utf-8")
    monkeypatch.setattr(ra, "ALERT_LOG_PATH", log_file)

    result = _load_alerts_log()
    assert len(result) == 1
    assert result[0]["domain"] == "game"


def test_load_alerts_log_empty_when_file_missing(tmp_path, monkeypatch):
    from engine.root_cause_analyzer import _load_alerts_log
    import engine.root_cause_analyzer as ra
    monkeypatch.setattr(ra, "ALERT_LOG_PATH", tmp_path / "nonexistent.log")
    assert _load_alerts_log() == []


def test_main_skips_when_insufficient_alerts(caplog):
    from engine.root_cause_analyzer import main
    import logging
    with patch("engine.root_cause_analyzer._load_metric_history", return_value=[]), \
         patch("engine.root_cause_analyzer._load_alerts_log", return_value=[]), \
         patch("engine.root_cause_analyzer.get_monitor_conn") as mock_conn:
        with caplog.at_level(logging.INFO, logger="engine.root_cause_analyzer"):
            main(interactive=False)
    mock_conn.assert_not_called()
    assert any("不足" in r.message for r in caplog.records)
