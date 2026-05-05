import json
import pytest
from unittest.mock import patch, MagicMock
from engine.models import MetricResult, RuleResult
from engine.alert_engine import handle
from engine.correlation_engine import CorrelationResult


def _make_result(level="critical", action="enqueue"):
    m = MetricResult(domain="payment", metric="recharge_success_rate", value=0.5)
    return RuleResult(level=level, action=action, metric=m,
                      threshold=0.7, message="充值成功率低")


def _make_correlation(confidence="known"):
    return CorrelationResult(
        confidence=confidence,
        cause_domain="log",
        cause_metric="mq_route_error_count",
        cause_ts=1746399628000,
        lead_minutes=6.2,
        message="已知因果：MQ 路由故障于 6.2 分钟前触发",
    )


def test_handle_writes_correlation_field_when_known(tmp_path, monkeypatch):
    import engine.alert_engine as ae
    log_file = tmp_path / "alerts.log"
    monkeypatch.setattr(ae, "ALERT_LOG_PATH", log_file)

    cr = _make_correlation("known")
    with patch("engine.alert_engine.correlate", return_value=cr):
        handle(_make_result("critical"))

    data = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert "correlation" in data
    assert data["correlation"]["confidence"] == "known"
    assert data["correlation"]["cause_domain"] == "log"
    assert "已知因果" in data["correlation"]["message"]


def test_handle_writes_correlation_field_when_suspected(tmp_path, monkeypatch):
    import engine.alert_engine as ae
    log_file = tmp_path / "alerts.log"
    monkeypatch.setattr(ae, "ALERT_LOG_PATH", log_file)

    cr = CorrelationResult(
        confidence="suspected",
        cause_domain="game",
        cause_metric="game_transfer_fail_rate",
        cause_ts=1746399628000,
        lead_minutes=3.1,
        message="时序相关（待核查）：game 域 game_transfer_fail_rate 于 3.1 分钟前有告警",
    )
    with patch("engine.alert_engine.correlate", return_value=cr):
        handle(_make_result("warning", "alert"))

    data = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert data["correlation"]["confidence"] == "suspected"
    assert "待核查" in data["correlation"]["message"]


def test_handle_omits_correlation_field_when_none(tmp_path, monkeypatch):
    import engine.alert_engine as ae
    log_file = tmp_path / "alerts.log"
    monkeypatch.setattr(ae, "ALERT_LOG_PATH", log_file)

    cr = CorrelationResult(
        confidence="none", cause_domain=None, cause_metric=None,
        cause_ts=None, lead_minutes=None, message="",
    )
    with patch("engine.alert_engine.correlate", return_value=cr):
        handle(_make_result("warning"))

    data = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert "correlation" not in data


def test_handle_ok_does_not_call_correlate():
    m = MetricResult(domain="payment", metric="recharge_success_rate", value=0.9)
    result = RuleResult(level="ok", action="none", metric=m, threshold=0.7, message="")
    with patch("engine.alert_engine.correlate") as mock_correlate:
        handle(result)
    mock_correlate.assert_not_called()
