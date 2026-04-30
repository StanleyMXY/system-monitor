import os
import time
from engine.models import MetricResult


def _get_prefix() -> str:
    return os.getenv("ES_INDEX_PREFIX", "q6")


def _count_errors(es, index_pattern: str, must_phrases: list[str], window_minutes: int) -> tuple[int, str]:
    """查询 ES 指定时间窗口内匹配所有短语的日志数量，返回 (count, 最新一条消息)。"""
    cutoff = f"now-{window_minutes}m"
    must = [{"match_phrase": {"message": p}} for p in must_phrases]
    must.append({"range": {"@timestamp": {"gte": cutoff}}})

    count_resp = es.count(index=index_pattern, body={"query": {"bool": {"must": must}}})
    count = count_resp["count"]

    sample = ""
    if count > 0:
        search_resp = es.search(
            index=index_pattern,
            body={
                "query": {"bool": {"must": must}},
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
