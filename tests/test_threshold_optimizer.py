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
