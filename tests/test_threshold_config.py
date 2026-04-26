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
