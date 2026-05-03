import pytest
from engine.threshold_optimizer import compute_stats


def make_rows(values_and_levels):
    """生成模拟的 DB 行列表，每行格式：{value, level, recorded_at}"""
    import time
    base = int(time.time() * 1000) - 30 * 86400 * 1000
    return [
        {"value": v, "level": l, "recorded_at": base + i * 300_000}
        for i, (v, l) in enumerate(values_and_levels)
    ]


def test_compute_stats_percentiles():
    # 生成 100 个元素（range(0, 100) 映射到 0.60~0.99 之间，循环取值）
    rows = make_rows([(0.60 + (i % 40) * 0.01, "ok") for i in range(100)])
    stats = compute_stats("recharge_success_rate", "payment", rows, {"warning": 0.80, "critical": 0.60})

    assert stats["metric"] == "recharge_success_rate"
    assert stats["domain"] == "payment"
    assert stats["sample_count"] == 100
    assert stats["p95"] > stats["p50"]
    assert stats["min"] == pytest.approx(0.60, abs=0.01)
    assert stats["max"] == pytest.approx(0.99, abs=0.02)
    assert stats["current_warning"] == 0.80
    assert stats["current_critical"] == 0.60


def test_compute_stats_alert_rate():
    rows = make_rows(
        [(0.95, "ok")] * 97 + [(0.75, "warning")] * 3
    )
    stats = compute_stats("recharge_success_rate", "payment", rows, {"warning": 0.80})

    assert stats["alert_count_30d"] == 3
    assert stats["alert_rate_30d"] == pytest.approx(3 / 100, abs=0.001)


def test_compute_stats_gap_positive_means_loose():
    # p95=0.871，warning=0.80 → 阈值偏宽松，gap 为正
    rows = make_rows([(0.87 + i * 0.001, "ok") for i in range(100)])
    stats = compute_stats("recharge_success_rate", "payment", rows, {"warning": 0.80})
    assert stats["gap_p95_vs_warning"] > 0


def test_compute_stats_insufficient_data_returns_none():
    rows = make_rows([(0.95, "ok")] * 5)  # 只有 5 条，不足 100
    result = compute_stats("recharge_success_rate", "payment", rows, {"warning": 0.80})
    assert result is None


def test_load_all_stats_groups_by_metric():
    from unittest.mock import MagicMock, patch
    import time

    now_ms = int(time.time() * 1000)
    base = now_ms - 20 * 86400 * 1000

    fake_rows = [
        {"domain": "payment", "metric": "recharge_success_rate",
         "channel_id": None, "value": 0.9 + i * 0.001, "level": "ok",
         "recorded_at": base + i * 300_000}
        for i in range(150)
    ]

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchall.return_value = fake_rows
    mock_conn.cursor.return_value = mock_cursor

    payment_thresholds = {
        "recharge": {"success_rate_warning": 0.80, "success_rate_critical": 0.60},
        "withdraw": {"queue_count_warning": 50, "fail_rate_warning": 0.20},
        "channel_account": {"balance_warning_amount": 10000},
    }

    with patch("engine.threshold_optimizer.get_monitor_conn", return_value=mock_conn):
        from engine.threshold_optimizer import load_all_stats
        result = load_all_stats({"payment": payment_thresholds})

    assert "payment" in result
    assert len(result["payment"]) > 0
    first = result["payment"][0]
    assert first["metric"] == "recharge_success_rate"
    assert first["sample_count"] == 150
