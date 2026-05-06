import json
import time
from pathlib import Path
from engine.models import MetricResult, RuleResult
from dashboard.monitor_dashboard import MonitorDashboard


def make_rule_result(level, action, domain="payment", metric="recharge_success_rate", value=0.5, threshold=0.8, msg="test"):
    m = MetricResult(domain=domain, metric=metric, value=value)
    return RuleResult(level=level, action=action, metric=m, threshold=threshold, message=msg)


def test_update_writes_html_file(tmp_path, monkeypatch):
    dashboard = MonitorDashboard(port=0)
    monkeypatch.setattr(dashboard, "_html_path", tmp_path / "dashboard.html")

    results = [
        make_rule_result("critical", "enqueue", msg="充值成功率严重低于阈值"),
        make_rule_result("warning", "alert", msg="渠道余额不足"),
        make_rule_result("ok", "none"),
    ]
    dashboard.update("payment", results)

    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "天工平台" in html
    assert "payment" in html
    assert "充值成功率严重低于阈值" in html
    assert "渠道余额不足" in html


def test_update_tracks_domain_summary(tmp_path, monkeypatch):
    dashboard = MonitorDashboard(port=0)
    monkeypatch.setattr(dashboard, "_html_path", tmp_path / "dashboard.html")

    results = [
        make_rule_result("critical", "enqueue", metric="recharge_success_rate"),
        make_rule_result("warning", "alert", metric="channel_balance"),
        make_rule_result("ok", "none", metric="withdraw_queue_count"),
    ]
    dashboard.update("payment", results)

    summary = dashboard.get_domain_summary("payment")
    assert summary["critical"] == 1
    assert summary["warning"] == 1
    assert summary["ok"] == 1


def test_recent_alerts_only_non_ok(tmp_path, monkeypatch):
    dashboard = MonitorDashboard(port=0)
    monkeypatch.setattr(dashboard, "_html_path", tmp_path / "dashboard.html")

    results = [
        make_rule_result("critical", "enqueue", msg="alert1"),
        make_rule_result("warning", "alert", msg="alert2"),
        make_rule_result("ok", "none", msg="ok_msg"),
    ]
    dashboard.update("payment", results)

    alerts = list(dashboard.recent_alerts)
    assert len(alerts) == 2
    assert all(r.level != "ok" for r in alerts)


def test_update_multiple_domains(tmp_path, monkeypatch):
    dashboard = MonitorDashboard(port=0)
    monkeypatch.setattr(dashboard, "_html_path", tmp_path / "dashboard.html")

    dashboard.update("payment", [make_rule_result("warning", "alert")])
    dashboard.update("game", [make_rule_result("ok", "none", domain="game")])

    assert "payment" in dashboard.domain_results
    assert "game" in dashboard.domain_results
