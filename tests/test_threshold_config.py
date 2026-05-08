import json
import pytest
from pathlib import Path


def test_load_thresholds_returns_dict_without_comment_keys(tmp_path, monkeypatch):
    cfg = {
        "_comment": "should be filtered",
        "recharge": {"success_rate_warning": 0.80}
    }
    cfg_file = tmp_path / "thresholds_payment.json"
    cfg_file.write_text(json.dumps(cfg), encoding="utf-8")

    import engine.threshold_config as tc
    monkeypatch.setattr(tc, "CONFIG_DIR", tmp_path)

    result = tc.load_thresholds("payment")
    assert "_comment" not in result
    assert result["recharge"]["success_rate_warning"] == 0.80


def test_load_thresholds_raises_on_missing_file(tmp_path, monkeypatch):
    import engine.threshold_config as tc
    monkeypatch.setattr(tc, "CONFIG_DIR", tmp_path)

    with pytest.raises(FileNotFoundError):
        tc.load_thresholds("nonexistent")


def test_metric_threshold_map_has_payment_metrics():
    from engine.threshold_config import METRIC_THRESHOLD_MAP
    assert "recharge_success_rate" in METRIC_THRESHOLD_MAP
    domain, sub_key, key_map = METRIC_THRESHOLD_MAP["recharge_success_rate"]
    assert domain == "payment"
    assert sub_key == "recharge"
    assert "warning" in key_map


def test_metric_threshold_map_covers_all_domains():
    from engine.threshold_config import METRIC_THRESHOLD_MAP
    domains = {v[0] for v in METRIC_THRESHOLD_MAP.values()}
    assert domains == {"payment", "game", "risk", "activity", "account", "operation"}
