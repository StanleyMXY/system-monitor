import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from config.db import get_monitor_conn
from engine.models import RuleResult

_CHAINS_PATH = Path(__file__).parent.parent / "config" / "causal_chains.json"


@dataclass
class CorrelationResult:
    confidence: Literal["known", "suspected", "none"]
    cause_domain: str | None
    cause_metric: str | None
    cause_ts: int | None
    lead_minutes: float | None
    message: str


def _load_chains() -> list[dict]:
    if not _CHAINS_PATH.exists():
        return []
    try:
        with open(_CHAINS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return []


def correlate(result: RuleResult) -> CorrelationResult:
    """实时关联分析：查近 30min 跨域告警，匹配已知因果链。"""
    if result.level == "ok":
        return CorrelationResult(
            confidence="none", cause_domain=None, cause_metric=None,
            cause_ts=None, lead_minutes=None, message="",
        )

    now_ms = int(time.time() * 1000)
    window_start_ms = now_ms - 30 * 60 * 1000
    window_end_ms = now_ms - 60 * 1000  # 排除 1 分钟内（避免同批采集的自关联）
    current_domain = result.metric.domain
    current_metric = result.metric.metric

    conn = get_monitor_conn()
    try:
        rows = _fetch_recent_alerts(conn, current_domain, window_start_ms, window_end_ms)
    finally:
        conn.close()

    if not rows:
        return CorrelationResult(
            confidence="none", cause_domain=None, cause_metric=None,
            cause_ts=None, lead_minutes=None, message="",
        )

    chains = _load_chains()

    # 优先匹配已知因果链
    for chain in chains:
        cause = chain["cause"]
        for effect in chain["effects"]:
            if effect["domain"] == current_domain and effect["metric"] == current_metric:
                for row in rows:
                    if row["domain"] == cause["domain"] and row["metric"] == cause["metric"]:
                        lead = (now_ms - row["recorded_at"]) / 60000
                        return CorrelationResult(
                            confidence="known",
                            cause_domain=cause["domain"],
                            cause_metric=cause["metric"],
                            cause_ts=row["recorded_at"],
                            lead_minutes=round(lead, 1),
                            message=(
                                f"已知因果：{chain['description']}，"
                                f"根因于 {round(lead, 1)} 分钟前触发"
                            ),
                        )

    # 降级：时序接近（取最近一条跨域告警）
    nearest = rows[0]
    lead = (now_ms - nearest["recorded_at"]) / 60000
    return CorrelationResult(
        confidence="suspected",
        cause_domain=nearest["domain"],
        cause_metric=nearest["metric"],
        cause_ts=nearest["recorded_at"],
        lead_minutes=round(lead, 1),
        message=(
            f"时序相关（待核查）：{nearest['domain']} 域 {nearest['metric']} "
            f"于 {round(lead, 1)} 分钟前有告警，可能关联"
        ),
    )


def _fetch_recent_alerts(conn, exclude_domain: str,
                         start_ms: int, end_ms: int) -> list[dict]:
    """从 monitor_metric_history 查近期跨域非 ok 告警，按时间倒序。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT domain, metric, channel_id, value, level, recorded_at
            FROM monitor_metric_history
            WHERE level != 'ok'
              AND domain != %s
              AND recorded_at BETWEEN %s AND %s
            ORDER BY recorded_at DESC
            """,
            (exclude_domain, start_ms, end_ms),
        )
        return list(cur.fetchall())
