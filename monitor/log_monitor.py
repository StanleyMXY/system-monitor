import json
import os
import time
from pathlib import Path
from engine.models import MetricResult


_NOISE_RULES_PATH = Path(__file__).parent.parent / "config" / "noise_rules.json"


def _load_noise_must_not() -> list[dict]:
    """加载噪音规则，返回 ES must_not 子句列表。每条规则所有 phrases 同时命中才过滤。"""
    if not _NOISE_RULES_PATH.exists():
        return []
    with open(_NOISE_RULES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    result = []
    for rule in data.get("rules", []):
        phrases = rule.get("must_phrases", [])
        if phrases:
            result.append({"bool": {"must": [
                {"match_phrase": {"message": p}} for p in phrases
            ]}})
    return result


def _get_prefix() -> str:
    return os.getenv("ES_INDEX_PREFIX", "q6")


def _count_errors(es, index_pattern: str, must_phrases: list[str], window_minutes: int) -> tuple[int, str]:
    """查询 ES 指定时间窗口内匹配所有短语的日志数量，返回 (count, 最新一条消息)。"""
    cutoff = f"now-{window_minutes}m"
    must = [{"match_phrase": {"message": p}} for p in must_phrases]
    must.append({"range": {"@timestamp": {"gte": cutoff}}})
    noise_must_not = _load_noise_must_not()

    query = {"bool": {"must": must}}
    if noise_must_not:
        query["bool"]["must_not"] = noise_must_not

    count_resp = es.count(index=index_pattern, body={"query": query})
    count = count_resp["count"]

    sample = ""
    if count > 0:
        search_resp = es.search(
            index=index_pattern,
            body={
                "query": query,
                "size": 1,
                "sort": [{"@timestamp": {"order": "desc"}}],
                "_source": ["message"],
            },
        )
        hits = search_resp["hits"]["hits"]
        if hits:
            sample = hits[0]["_source"]["message"].split("\n")[0][:200]

    return count, sample


def check_game_launch_error(es, thresholds: dict) -> list[MetricResult]:
    """采集游戏启动失败次数（LaunchController IOException/500）。"""
    cfg = thresholds["high_priority"]
    prefix = _get_prefix()
    count, sample = _count_errors(
        es,
        f"{prefix}app-*",
        ["LaunchController", "IOException"],
        cfg["check_interval_minutes"],
    )
    return [MetricResult(
        domain="log", metric="game_launch_error_count",
        value=float(count),
        extra={"sample": sample},
    )]


def check_mq_route_error(es, thresholds: dict) -> list[MetricResult]:
    """采集 MQ exchange 路由失败次数（PRECONDITION_FAILED）。"""
    cfg = thresholds["high_priority"]
    prefix = _get_prefix()
    count, _ = _count_errors(
        es,
        f"{prefix}app-*,{prefix}mw-*",
        ["PRECONDITION_FAILED", "Cannot route message"],
        cfg["check_interval_minutes"],
    )
    return [MetricResult(
        domain="log", metric="mq_route_error_count",
        value=float(count),
    )]


def check_db_shard_error(es, thresholds: dict) -> list[MetricResult]:
    """采集 ShardingSphere 分表路由失败次数。"""
    cfg = thresholds["high_priority"]
    prefix = _get_prefix()
    count, _ = _count_errors(
        es,
        f"{prefix}playgame-*",
        ["ShardingSphereException"],
        cfg["check_interval_minutes"],
    )
    return [MetricResult(
        domain="log", metric="db_shard_error_count",
        value=float(count),
    )]


def check_websocket_error(es, thresholds: dict) -> list[MetricResult]:
    """采集 WebSocket 严重异常次数。"""
    cfg = thresholds["low_priority"]
    prefix = _get_prefix()
    count, _ = _count_errors(
        es,
        f"{prefix}app-*",
        ["SocketServer", "严重异常"],
        cfg["check_interval_minutes"],
    )
    return [MetricResult(
        domain="log", metric="websocket_error_count",
        value=float(count),
    )]


def check_db_duplicate_error(es, thresholds: dict) -> list[MetricResult]:
    """采集数据库唯一键冲突次数。"""
    cfg = thresholds["low_priority"]
    prefix = _get_prefix()
    count, _ = _count_errors(
        es,
        f"{prefix}app-*",
        ["SQLIntegrityConstraintViolation"],
        cfg["check_interval_minutes"],
    )
    return [MetricResult(
        domain="log", metric="db_duplicate_error_count",
        value=float(count),
    )]
