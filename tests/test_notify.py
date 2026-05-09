from unittest.mock import MagicMock, patch


# ── tg_client ──────────────────────────────────────────────────────────────

def test_send_alert_skips_when_not_configured(caplog):
    with patch.dict("os.environ", {"TG_BOT_TOKEN": "", "TG_ALERT_CHAT_IDS": ""}):
        from notify import tg_client
        import importlib
        importlib.reload(tg_client)
        import logging
        with caplog.at_level(logging.WARNING, logger="notify.tg_client"):
            tg_client.send_alert("test")
    assert "未配置" in caplog.text


def test_send_alert_posts_to_all_chat_ids():
    env = {
        "TG_BOT_TOKEN": "testtoken",
        "TG_ALERT_CHAT_IDS": "111,222",
    }
    with patch.dict("os.environ", env):
        with patch("notify.tg_client.requests.post") as mock_post:
            mock_post.return_value.json.return_value = {"ok": True}
            from notify import tg_client
            import importlib
            importlib.reload(tg_client)
            tg_client.send_alert("hello")
    assert mock_post.call_count == 2
    chat_ids_called = {c.kwargs["json"]["chat_id"] for c in mock_post.call_args_list}
    assert chat_ids_called == {"111", "222"}


def test_send_alert_survives_request_exception():
    env = {"TG_BOT_TOKEN": "tok", "TG_ALERT_CHAT_IDS": "123"}
    with patch.dict("os.environ", env):
        with patch("notify.tg_client.requests.post", side_effect=Exception("timeout")):
            from notify import tg_client
            import importlib
            importlib.reload(tg_client)
            tg_client.send_alert("oops")  # must not raise


# ── alerts ──────────────────────────────────────────────────────────────────

def test_notify_watchdog_alert_skips_when_disabled(caplog):
    with patch.dict("os.environ", {"TG_NOTIFY_ENABLED": "false"}):
        with patch("notify.alerts.send_alert") as mock_send:
            from notify import alerts
            import importlib
            importlib.reload(alerts)
            alerts.notify_watchdog_alert("2026-01-01 00:00:00", 15)
    mock_send.assert_not_called()


def test_notify_watchdog_alert_sends_when_enabled():
    # 不 reload——env 在调用时读取，patch.dict 即时生效
    from notify import alerts
    with patch.dict("os.environ", {"TG_NOTIFY_ENABLED": "true"}):
        with patch("notify.alerts.send_alert") as mock_send:
            alerts.notify_watchdog_alert("2026-01-01 00:00:00", 15)
    mock_send.assert_called_once()
    text = mock_send.call_args[0][0]
    assert "15" in text
    assert "2026-01-01 00:00:00" in text


# ── notify_critical_alert ───────────────────────────────────────────────────

def _make_corr(confidence, cause_domain=None, cause_metric=None, lead_minutes=None):
    from unittest.mock import MagicMock
    cr = MagicMock()
    cr.confidence = confidence
    cr.cause_domain = cause_domain
    cr.cause_metric = cause_metric
    cr.lead_minutes = lead_minutes
    return cr


def test_notify_critical_alert_skips_when_disabled():
    from notify import alerts
    with patch.dict("os.environ", {"TG_NOTIFY_ENABLED": "false"}):
        with patch("notify.alerts.send_alert") as mock_send:
            alerts.notify_critical_alert("充值成功率严重低于阈值", _make_corr("none"))
    mock_send.assert_not_called()


def test_notify_critical_alert_sends_without_correlation():
    from notify import alerts
    with patch.dict("os.environ", {"TG_NOTIFY_ENABLED": "true"}):
        with patch("notify.alerts.send_alert") as mock_send:
            alerts.notify_critical_alert("充值成功率严重低于阈值", _make_corr("none"))
    mock_send.assert_called_once()
    assert "Critical" in mock_send.call_args[0][0]
    assert "根因" not in mock_send.call_args[0][0]


def test_notify_critical_alert_includes_known_correlation():
    from notify import alerts
    cr = _make_corr("known", "game", "game_transfer_fail_rate", lead_minutes=18.0)
    with patch.dict("os.environ", {"TG_NOTIFY_ENABLED": "true"}):
        with patch("notify.alerts.send_alert") as mock_send:
            alerts.notify_critical_alert("充值成功率严重低于阈值", cr)
    text = mock_send.call_args[0][0]
    assert "已知根因" in text
    assert "game.game_transfer_fail_rate" in text
    assert "18" in text


def test_notify_critical_alert_includes_suspected_correlation():
    from notify import alerts
    cr = _make_corr("suspected", "payment", "channel_balance", lead_minutes=5.0)
    with patch.dict("os.environ", {"TG_NOTIFY_ENABLED": "true"}):
        with patch("notify.alerts.send_alert") as mock_send:
            alerts.notify_critical_alert("消息", cr)
    text = mock_send.call_args[0][0]
    assert "疑似根因" in text


# ── alert_engine critical → TG ───────────────────────────────────────────────

def test_alert_engine_calls_tg_on_critical(tmp_path):
    from engine.models import MetricResult, RuleResult
    from engine import alert_engine
    import importlib
    importlib.reload(alert_engine)
    alert_engine.ALERT_LOG_PATH = tmp_path / "alerts.log"

    metric = MetricResult(domain="payment", metric="recharge_success_rate", value=0.1)
    result = RuleResult(level="critical", action="enqueue", metric=metric,
                        threshold=0.6, message="充值成功率严重低于阈值")

    with patch("engine.alert_engine.correlate") as mock_corr:
        mock_corr.return_value = _make_corr("none")
        with patch("notify.alerts.notify_critical_alert") as mock_notify:
            alert_engine.handle(result)

    mock_notify.assert_called_once()


def test_alert_engine_does_not_call_tg_on_warning(tmp_path):
    from engine.models import MetricResult, RuleResult
    from engine import alert_engine
    import importlib
    importlib.reload(alert_engine)
    alert_engine.ALERT_LOG_PATH = tmp_path / "alerts.log"

    metric = MetricResult(domain="payment", metric="recharge_success_rate", value=0.7)
    result = RuleResult(level="warning", action="alert", metric=metric,
                        threshold=0.8, message="充值成功率低于预警线")

    with patch("engine.alert_engine.correlate") as mock_corr:
        mock_corr.return_value = _make_corr("none")
        with patch("notify.alerts.notify_critical_alert") as mock_notify:
            alert_engine.handle(result)

    mock_notify.assert_not_called()


# ── watchdog logic ──────────────────────────────────────────────────────────

def _make_watchdog_conn(last_beat_at):
    cursor = MagicMock()
    cursor.fetchone.return_value = {"last_beat_at": last_beat_at} if last_beat_at else None
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


def test_watchdog_no_alert_when_fresh():
    from datetime import datetime, timedelta
    fresh = datetime.now() - timedelta(minutes=2)

    with patch("config.db.get_monitor_conn", return_value=_make_watchdog_conn(fresh)):
        with patch("notify.alerts.notify_watchdog_alert") as mock_alert:
            from scripts import watchdog
            import importlib
            importlib.reload(watchdog)
            watchdog.main()
    mock_alert.assert_not_called()


def test_watchdog_alerts_when_stale():
    import pytest
    from datetime import datetime, timedelta
    from scripts import watchdog
    import importlib
    importlib.reload(watchdog)
    stale = datetime.now() - timedelta(minutes=15)

    with patch("config.db.get_monitor_conn", return_value=_make_watchdog_conn(stale)):
        with patch("notify.alerts.notify_watchdog_alert") as mock_alert:
            with pytest.raises(SystemExit):
                watchdog.main()
    mock_alert.assert_called_once()
    elapsed = mock_alert.call_args[0][1]
    assert elapsed >= 10


def test_watchdog_alerts_when_no_row():
    import pytest
    from scripts import watchdog
    import importlib
    importlib.reload(watchdog)
    with patch("config.db.get_monitor_conn", return_value=_make_watchdog_conn(None)):
        with patch("notify.alerts.notify_watchdog_alert") as mock_alert:
            with pytest.raises(SystemExit):
                watchdog.main()
    mock_alert.assert_called_once()
