import json
import logging
from unittest.mock import patch
from engine.models import MetricResult, RuleResult
from engine.alert_engine import handle
from engine.correlation_engine import CorrelationResult


def _no_correlation():
    return CorrelationResult(
        confidence="none", cause_domain=None, cause_metric=None,
        cause_ts=None, lead_minutes=None, message="",
    )


def make_rule_result(level, action, metric_name="recharge_success_rate", value=0.5, threshold=0.8):
    metric = MetricResult(domain="payment", metric=metric_name, value=value)
    return RuleResult(level=level, action=action, metric=metric, threshold=threshold,
                      message=f"测试告警: {metric_name} = {value}")


def test_handle_ok_does_nothing(caplog):
    result = make_rule_result("ok", "none")
    with caplog.at_level(logging.DEBUG):
        handle(result)
    assert "告警" not in caplog.text


def test_handle_warning_logs(caplog):
    result = make_rule_result("warning", "alert")
    with patch("engine.alert_engine.correlate", return_value=_no_correlation()):
        with caplog.at_level(logging.WARNING):
            handle(result)
    assert "WARNING" in caplog.text or "warning" in caplog.text.lower()


def test_handle_critical_logs(caplog):
    result = make_rule_result("critical", "enqueue")
    with patch("engine.alert_engine.correlate", return_value=_no_correlation()):
        with caplog.at_level(logging.CRITICAL):
            handle(result)
    assert "CRITICAL" in caplog.text or "critical" in caplog.text.lower()


def test_handle_warning_writes_to_file(tmp_path, monkeypatch):
    import engine.alert_engine as ae
    log_file = tmp_path / "alerts.log"
    monkeypatch.setattr(ae, "ALERT_LOG_PATH", log_file)

    result = make_rule_result("warning", "alert", value=0.75, threshold=0.80)
    with patch("engine.alert_engine.correlate", return_value=_no_correlation()):
        handle(result)

    content = log_file.read_text(encoding="utf-8")
    data = json.loads(content.strip())
    assert data["level"] == "warning"
    assert data["metric"] == "recharge_success_rate"
    assert data["value"] == 0.75


def test_handle_ok_does_not_write_to_file(tmp_path, monkeypatch):
    import engine.alert_engine as ae
    log_file = tmp_path / "alerts.log"
    monkeypatch.setattr(ae, "ALERT_LOG_PATH", log_file)

    result = make_rule_result("ok", "none")
    handle(result)

    assert not log_file.exists()
