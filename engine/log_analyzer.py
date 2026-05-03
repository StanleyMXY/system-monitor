import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent.parent / "output"
_NOISE_RULES_PATH = Path(__file__).parent.parent / "config" / "noise_rules.json"

_EXISTING_RULES_SUMMARY = """现有监控规则（已覆盖，请勿重复建议）：
1. 游戏启动失败：LaunchController + IOException
2. MQ 路由失败：PRECONDITION_FAILED + Cannot route message
3. 分表路由失败：ShardingSphereException
4. WebSocket 严重异常：SocketServer + 严重异常
5. DB 唯一键冲突：SQLIntegrityConstraintViolation
"""


def _load_noise_rules() -> list[dict]:
    """加载噪音规则列表（原始规则对象）。"""
    if not _NOISE_RULES_PATH.exists():
        return []
    with open(_NOISE_RULES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("rules", [])


def _candidate_matches_noise(term: str, noise_rules: list[dict]) -> bool:
    """检查候选词条是否命中任一噪音规则（所有 must_phrases 都出现在 term 中）。"""
    for rule in noise_rules:
        phrases = rule.get("must_phrases", [])
        if phrases and all(p.lower() in term.lower() for p in phrases):
            return True
    return False


def fetch_candidates(es, prefix: str) -> list[dict]:
    """
    对 ES 执行 significant_terms 聚合，返回经噪音预过滤的候选模式列表。
    每个候选：{"term": str, "doc_count": int, "score": float, "samples": list[str]}
    """
    index = f"{prefix}*"
    agg_body = {
        "query": {
            "bool": {
                "must": [
                    {"range": {"@timestamp": {"gte": "now-24h"}}},
                    {"bool": {"should": [
                        {"match": {"message": "WARN"}},
                        {"match": {"message": "ERROR"}},
                        {"match": {"message": "Exception"}},
                    ], "minimum_should_match": 1}},
                ]
            }
        },
        "aggs": {
            "new_patterns": {
                "significant_terms": {
                    "field": "message",
                    "size": 20,
                    "background_filter": {
                        "range": {"@timestamp": {"gte": "now-7d"}}
                    },
                    "min_doc_count": 5,
                }
            }
        },
        "size": 0,
    }

    resp = es.search(index=index, body=agg_body)
    buckets = resp.get("aggregations", {}).get("new_patterns", {}).get("buckets", [])

    noise_rules = _load_noise_rules()
    candidates = []

    for bucket in buckets:
        term = bucket["key"]
        if _candidate_matches_noise(term, noise_rules):
            logger.info(f"[log_analyzer] 噪音预过滤跳过: {term[:60]}")
            continue

        # 拉取样本
        sample_resp = es.search(index=index, body={
            "query": {"bool": {"must": [
                {"match": {"message": term}},
                {"range": {"@timestamp": {"gte": "now-24h"}}},
            ]}},
            "size": 3,
            "sort": [{"@timestamp": {"order": "desc"}}],
            "_source": ["message", "@timestamp"],
        })
        samples = [
            f"{h['_source'].get('@timestamp', '')}  {h['_source'].get('message', '').split(chr(10))[0][:150]}"
            for h in sample_resp["hits"]["hits"]
        ]

        candidates.append({
            "term": term,
            "doc_count": bucket["doc_count"],
            "score": round(bucket.get("score", 0), 2),
            "samples": samples,
        })

    return candidates
