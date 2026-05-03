import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── 噪音过滤测试 ──────────────────────────────────────────────────────────────

def test_load_noise_must_not_returns_empty_when_no_file(tmp_path, monkeypatch):
    """noise_rules.json 不存在时返回空列表。"""
    import monitor.log_monitor as lm
    monkeypatch.setattr(lm, "_NOISE_RULES_PATH", tmp_path / "nonexistent.json")
    result = lm._load_noise_must_not()
    assert result == []


def test_load_noise_must_not_single_rule(tmp_path, monkeypatch):
    """单条规则：两个 phrases 应组成一个 bool.must 子句。"""
    noise_file = tmp_path / "noise_rules.json"
    noise_file.write_text(json.dumps({
        "rules": [{
            "id": "noise_001",
            "description": "test",
            "must_phrases": ["risk_task_fail_Delay", "PRECONDITION_FAILED"]
        }]
    }), encoding="utf-8")

    import monitor.log_monitor as lm
    monkeypatch.setattr(lm, "_NOISE_RULES_PATH", noise_file)
    result = lm._load_noise_must_not()

    assert len(result) == 1
    assert result[0] == {"bool": {"must": [
        {"match_phrase": {"message": "risk_task_fail_Delay"}},
        {"match_phrase": {"message": "PRECONDITION_FAILED"}},
    ]}}


def test_load_noise_must_not_multiple_rules(tmp_path, monkeypatch):
    """多条规则应各自生成独立的 bool.must 子句。"""
    noise_file = tmp_path / "noise_rules.json"
    noise_file.write_text(json.dumps({
        "rules": [
            {"id": "n1", "description": "a", "must_phrases": ["AAA", "BBB"]},
            {"id": "n2", "description": "b", "must_phrases": ["CCC"]},
        ]
    }), encoding="utf-8")

    import monitor.log_monitor as lm
    monkeypatch.setattr(lm, "_NOISE_RULES_PATH", noise_file)
    result = lm._load_noise_must_not()

    assert len(result) == 2
    assert result[1] == {"bool": {"must": [{"match_phrase": {"message": "CCC"}}]}}


def test_count_errors_injects_must_not(tmp_path, monkeypatch):
    """_count_errors 应将噪音规则注入 ES 查询的 must_not 子句。"""
    noise_file = tmp_path / "noise_rules.json"
    noise_file.write_text(json.dumps({
        "rules": [{"id": "n1", "description": "x", "must_phrases": ["BadThing"]}]
    }), encoding="utf-8")

    import monitor.log_monitor as lm
    monkeypatch.setattr(lm, "_NOISE_RULES_PATH", noise_file)

    captured = {}

    def fake_count(index, body):
        captured["body"] = body
        return {"count": 0}

    def fake_search(index, body):
        return {"hits": {"hits": []}}

    mock_es = MagicMock()
    mock_es.count.side_effect = fake_count
    mock_es.search.side_effect = fake_search

    lm._count_errors(mock_es, "test-*", ["LaunchController"], 5)

    query = captured["body"]["query"]["bool"]
    assert "must_not" in query
    must_not = query["must_not"]
    assert any(
        clause == {"bool": {"must": [{"match_phrase": {"message": "BadThing"}}]}}
        for clause in must_not
    )


# ── ES 聚合层测试 ──────────────────────────────────────────────────────────────

def test_fetch_candidates_returns_top_terms():
    """fetch_candidates 应从 ES significant_terms 响应中提取候选模式。"""
    from engine.log_analyzer import fetch_candidates

    fake_agg_resp = {
        "aggregations": {
            "new_patterns": {
                "buckets": [
                    {"key": "IOException LaunchController", "doc_count": 120, "score": 8.5},
                    {"key": "PRECONDITION_FAILED Cannot route", "doc_count": 45, "score": 3.2},
                ]
            }
        }
    }
    fake_search_resp = {
        "hits": {"hits": [
            {"_source": {"message": "sample log line", "@timestamp": "2026-05-03T03:00:00Z"}}
        ]}
    }

    mock_es = MagicMock()
    mock_es.search.side_effect = [fake_agg_resp, fake_search_resp, fake_search_resp]

    candidates = fetch_candidates(mock_es, prefix="q6")

    assert len(candidates) == 2
    assert candidates[0]["term"] == "IOException LaunchController"
    assert candidates[0]["doc_count"] == 120
    assert candidates[0]["score"] == 8.5
    assert "samples" in candidates[0]


def test_fetch_candidates_filters_known_noise(tmp_path, monkeypatch):
    """fetch_candidates 应跳过已在噪音规则库中的候选。"""
    noise_file = tmp_path / "noise_rules.json"
    noise_file.write_text(json.dumps({
        "rules": [{"id": "n1", "description": "x", "must_phrases": ["risk_task_fail_Delay"]}]
    }), encoding="utf-8")

    from engine import log_analyzer
    monkeypatch.setattr(log_analyzer, "_NOISE_RULES_PATH", noise_file)

    fake_agg_resp = {
        "aggregations": {
            "new_patterns": {
                "buckets": [
                    {"key": "risk_task_fail_Delay PRECONDITION", "doc_count": 500, "score": 2.1},
                    {"key": "ShardingSphereException", "doc_count": 30, "score": 5.0},
                ]
            }
        }
    }
    fake_search_resp = {
        "hits": {"hits": [
            {"_source": {"message": "shard error", "@timestamp": "2026-05-03T03:00:00Z"}}
        ]}
    }

    mock_es = MagicMock()
    mock_es.search.side_effect = [fake_agg_resp, fake_search_resp]

    candidates = log_analyzer.fetch_candidates(mock_es, prefix="q6")

    # risk_task_fail_Delay 的候选应被过滤掉，只剩 ShardingSphereException
    assert len(candidates) == 1
    assert candidates[0]["term"] == "ShardingSphereException"
