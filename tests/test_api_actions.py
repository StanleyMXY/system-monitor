# tests/test_api_actions.py
import json
import pytest
from pathlib import Path


def test_apply_threshold_writes_json(tmp_path):
    from api.actions.threshold import apply_threshold

    cfg = {
        "_comment": "test",
        "recharge": {
            "success_rate_warning": 0.80,
            "success_rate_critical": 0.60,
        }
    }
    cfg_file = tmp_path / "thresholds_payment.json"
    cfg_file.write_text(json.dumps(cfg), encoding="utf-8")

    apply_threshold(
        metric="recharge_success_rate",
        suggested={"warning": 0.85, "critical": 0.65},
        config_dir=tmp_path,
    )

    result = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert result["recharge"]["success_rate_warning"] == 0.85
    assert result["recharge"]["success_rate_critical"] == 0.65


def test_apply_threshold_unknown_metric_raises(tmp_path):
    from api.actions.threshold import apply_threshold
    with pytest.raises(ValueError, match="unknown metric"):
        apply_threshold(metric="nonexistent_metric", suggested={"warning": 1.0}, config_dir=tmp_path)


def test_append_noise_rule_creates_file(tmp_path):
    from api.actions.noise import append_noise_rule

    noise_path = tmp_path / "noise_rules.json"
    append_noise_rule(
        term="risk_task_fail_Delay",
        description="RabbitMQ 延迟队列失败",
        suggested_phrases=["risk_task_fail", "Delay"],
        noise_path=noise_path,
    )

    data = json.loads(noise_path.read_text(encoding="utf-8"))
    assert len(data["rules"]) == 1
    assert data["rules"][0]["must_phrases"] == ["risk_task_fail", "Delay"]


def test_append_noise_rule_increments_id(tmp_path):
    from api.actions.noise import append_noise_rule

    noise_path = tmp_path / "noise_rules.json"
    append_noise_rule("term1", "desc1", ["phrase1"], noise_path)
    append_noise_rule("term2", "desc2", ["phrase2"], noise_path)

    data = json.loads(noise_path.read_text(encoding="utf-8"))
    ids = [r["id"] for r in data["rules"]]
    assert ids == ["noise_001", "noise_002"]


def test_append_causal_chain_creates_file(tmp_path):
    from api.actions.chain import append_causal_chain

    chains_path = tmp_path / "causal_chains.json"
    chain = {"cause_domain": "payment", "cause_metric": "recharge_success_rate",
             "effect_domain": "game", "effect_metric": "game_transfer_fail_rate",
             "description": "test chain"}
    append_causal_chain(chain, chains_path)

    data = json.loads(chains_path.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["cause_domain"] == "payment"
