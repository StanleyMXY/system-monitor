import json
import time
from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest

from engine.models import MetricResult, RuleResult
from engine.correlation_engine import CorrelationResult, correlate


def _make_result(domain="payment", metric="recharge_success_rate",
                 level="critical", action="enqueue"):
    m = MetricResult(domain=domain, metric=metric, value=0.5)
    return RuleResult(level=level, action=action, metric=m,
                      threshold=0.7, message="test alert")


def test_correlation_result_fields():
    cr = CorrelationResult(
        confidence="none",
        cause_domain=None,
        cause_metric=None,
        cause_ts=None,
        lead_minutes=None,
        message="",
    )
    assert cr.confidence == "none"
    assert cr.cause_domain is None
    assert cr.message == ""


def test_correlate_ok_returns_none_confidence():
    result = _make_result(level="ok", action="none")
    with patch("engine.correlation_engine.get_monitor_conn") as mock_conn:
        cr = correlate(result)
    mock_conn.assert_not_called()
    assert cr.confidence == "none"
    assert cr.message == ""


def test_correlate_known_chain_match(tmp_path, monkeypatch):
    chains = [
        {
            "cause": {"domain": "log", "metric": "mq_route_error_count"},
            "effects": [{"domain": "payment", "metric": "recharge_success_rate"}],
            "description": "MQ 路由故障 → 支付成功率下降",
        }
    ]
    chains_file = tmp_path / "causal_chains.json"
    chains_file.write_text(json.dumps(chains), encoding="utf-8")

    import engine.correlation_engine as ce
    monkeypatch.setattr(ce, "_CHAINS_PATH", chains_file)

    now_ms = int(time.time() * 1000)
    cause_ts = now_ms - 10 * 60 * 1000  # 10 分钟前

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.description = [("domain",), ("metric",), ("channel_id",),
                                ("value",), ("level",), ("recorded_at",)]
    mock_cursor.fetchall.return_value = [
        ("log", "mq_route_error_count", None, 15.0, "critical", cause_ts)
    ]
    mock_conn.cursor.return_value = mock_cursor

    result = _make_result(domain="payment", metric="recharge_success_rate", level="critical")
    with patch("engine.correlation_engine.get_monitor_conn", return_value=mock_conn):
        cr = correlate(result)

    assert cr.confidence == "known"
    assert cr.cause_domain == "log"
    assert cr.cause_metric == "mq_route_error_count"
    assert cr.lead_minutes == pytest.approx(10.0, abs=0.5)
    assert "已知因果" in cr.message


def test_correlate_no_history_returns_none():
    result = _make_result(level="critical")
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchall.return_value = []
    mock_cursor.description = [("domain",), ("metric",), ("channel_id",),
                                ("value",), ("level",), ("recorded_at",)]
    mock_conn.cursor.return_value = mock_cursor

    with patch("engine.correlation_engine.get_monitor_conn", return_value=mock_conn):
        cr = correlate(result)

    assert cr.confidence == "none"
    assert cr.message == ""


def test_correlate_suspected_when_no_chain_match(tmp_path, monkeypatch):
    import engine.correlation_engine as ce
    monkeypatch.setattr(ce, "_CHAINS_PATH", tmp_path / "empty.json")
    (tmp_path / "empty.json").write_text("[]", encoding="utf-8")

    now_ms = int(time.time() * 1000)
    other_ts = now_ms - 5 * 60 * 1000

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.description = [("domain",), ("metric",), ("channel_id",),
                                ("value",), ("level",), ("recorded_at",)]
    mock_cursor.fetchall.return_value = [
        ("game", "game_transfer_fail_rate", None, 0.3, "warning", other_ts)
    ]
    mock_conn.cursor.return_value = mock_cursor

    result = _make_result(domain="payment", metric="recharge_success_rate", level="warning")
    with patch("engine.correlation_engine.get_monitor_conn", return_value=mock_conn):
        cr = correlate(result)

    assert cr.confidence == "suspected"
    assert cr.cause_domain == "game"
    assert "待核查" in cr.message


def test_correlate_suspected_when_chains_file_missing(tmp_path, monkeypatch):
    import engine.correlation_engine as ce
    monkeypatch.setattr(ce, "_CHAINS_PATH", tmp_path / "nonexistent.json")

    now_ms = int(time.time() * 1000)
    other_ts = now_ms - 8 * 60 * 1000

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.description = [("domain",), ("metric",), ("channel_id",),
                                ("value",), ("level",), ("recorded_at",)]
    mock_cursor.fetchall.return_value = [
        ("log", "mq_route_error_count", None, 10.0, "critical", other_ts)
    ]
    mock_conn.cursor.return_value = mock_cursor

    result = _make_result(domain="payment", metric="recharge_success_rate", level="warning")
    with patch("engine.correlation_engine.get_monitor_conn", return_value=mock_conn):
        cr = correlate(result)

    assert cr.confidence == "suspected"
    assert cr.cause_domain == "log"
    assert "待核查" in cr.message
