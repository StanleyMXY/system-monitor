import json
import logging
import time
from collections import defaultdict
from pathlib import Path

from config.db import get_monitor_conn

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"
OUTPUT_DIR = Path(__file__).parent.parent / "output"

_DOMAIN_DESCRIPTIONS = {
    "payment": "支付域负责充值、提现、渠道账号余额管理。critical 级别影响资金安全，warning 提示运营关注。",
    "game": "游戏供应商域负责游戏余额转账和厂商对账。critical 级别表示对账差异持续累积。",
    "risk": "风控域负责高危预警和风控事件处理。所有告警需人工及时响应。",
    "activity": "活动域负责兑换任务和首充监控。告警表示活动配置或流程异常。",
    "account": "用户账户域监控冻结余额异常增长。warning 提示可能存在批量操作风险。",
    "operation": "系统操作域监控 VIP 调整、余额调整、配置变更。enqueue 类告警需人工审批。",
}

_MIN_SAMPLES = 100


def compute_stats(
    metric: str,
    domain: str,
    rows: list[dict],
    thresholds: dict,
) -> dict | None:
    """
    对单个指标的历史行列表计算统计摘要。
    rows: list of {"value": float, "level": str, "recorded_at": int}
    thresholds: 该指标的阈值 dict，如 {"warning": 0.80, "critical": 0.60}
    返回 None 表示数据不足。
    """
    if len(rows) < _MIN_SAMPLES:
        return None

    values = [float(r["value"]) for r in rows]
    sorted_vals = sorted(values)
    n = len(sorted_vals)

    def percentile(p):
        idx = int(n * p / 100)
        return sorted_vals[min(idx, n - 1)]

    p50 = percentile(50)
    p95 = percentile(95)
    p99 = percentile(99)

    alert_levels = {"warning", "critical"}
    alert_rows = [r for r in rows if r["level"] in alert_levels]

    cutoff_7d = int(time.time() * 1000) - 7 * 86400 * 1000
    alert_7d = [r for r in alert_rows if r["recorded_at"] >= cutoff_7d]

    alert_count_30d = len(alert_rows)
    alert_rate_30d = alert_count_30d / n
    alert_rate_7d = len(alert_7d) / max(
        len([r for r in rows if r["recorded_at"] >= cutoff_7d]), 1
    )

    current_warning = thresholds.get("warning")
    current_critical = thresholds.get("critical")
    gap = (p95 - current_warning) if current_warning is not None else None

    return {
        "metric": metric,
        "domain": domain,
        "sample_count": n,
        "p50": round(p50, 4),
        "p95": round(p95, 4),
        "p99": round(p99, 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "alert_rate_7d": round(alert_rate_7d, 6),
        "alert_rate_30d": round(alert_rate_30d, 6),
        "alert_count_30d": alert_count_30d,
        "current_warning": current_warning,
        "current_critical": current_critical,
        "gap_p95_vs_warning": round(gap, 4) if gap is not None else None,
    }


_METRIC_THRESHOLD_MAP = {
    # payment
    "recharge_success_rate": ("payment", "recharge", {"warning": "success_rate_warning", "critical": "success_rate_critical"}),
    "recharge_timeout_rate": ("payment", "recharge", {"warning": "timeout_rate_warning"}),
    "recharge_pending_count": ("payment", "recharge", {"warning": "pending_count_warning"}),
    "channel_balance": ("payment", "channel_account", {"warning": "balance_warning_amount"}),
    "withdraw_queue_count": ("payment", "withdraw", {"warning": "queue_count_warning"}),
    "withdraw_fail_rate": ("payment", "withdraw", {"warning": "fail_rate_warning"}),
    # game
    "game_transfer_fail_rate": ("game", "balance_transfer", {"warning": "fail_rate_warning"}),
    "game_transfer_retry_count": ("game", "balance_transfer", {"warning": "retry_order_count_warning"}),
    "game_reconciliation_diff_days": ("game", "reconciliation", {"critical": "diff_consecutive_days_critical"}),
    # risk
    "risk_alert_backlog_count": ("risk", "alert", {"warning": "high_risk_pending_warning"}),
    "risk_alert_timeout_count": ("risk", "alert", {}),
    "risk_event_backlog_count": ("risk", "event", {"warning": "high_risk_pending_warning"}),
    "risk_blacklist_expiry_count": ("risk", "blacklist", {}),
    # activity
    "redemption_fail_rate": ("activity", "redemption", {"warning": "fail_rate_warning"}),
    "first_deposit_fail_count": ("activity", "first_deposit", {"warning": "fail_alert_threshold"}),
    # account
    "frozen_balance_growth_rate": ("account", "frozen_balance", {"warning": "growth_rate_warning"}),
    # operation
    "vip_adjust_count": ("operation", "vip_adjust", {"warning": "batch_count_per_hour_warning"}),
    "balance_adjustment_large_count": ("operation", "balance_adjustment", {}),
    "config_change_count": ("operation", "config_change", {"warning": "change_count_per_hour_warning"}),
}


def _extract_threshold(domain_cfg: dict, sub_key: str, key_map: dict) -> dict:
    """从域阈值 JSON 提取 warning/critical 数值。"""
    sub = domain_cfg.get(sub_key, {})
    result = {}
    for role, json_key in key_map.items():
        if json_key in sub:
            result[role] = sub[json_key]
    return result


def load_all_stats(domain_thresholds: dict) -> dict[str, list[dict]]:
    """
    从 monitor_metric_history 读取过去 30 天数据，
    对每个指标计算统计摘要，按域分组返回。
    domain_thresholds: {domain: thresholds_dict}
    返回: {domain: [stats_dict, ...]}
    """
    cutoff = int((time.time() - 30 * 86400) * 1000)
    conn = get_monitor_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT domain, metric, channel_id, value, level, recorded_at
                FROM monitor_metric_history
                WHERE recorded_at >= %s
                ORDER BY metric, recorded_at
                """,
                (cutoff,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    grouped: dict[str, list] = defaultdict(list)
    for row in rows:
        grouped[row["metric"]].append(row)

    result: dict[str, list] = defaultdict(list)
    for metric, metric_rows in grouped.items():
        if metric not in _METRIC_THRESHOLD_MAP:
            continue
        domain, sub_key, key_map = _METRIC_THRESHOLD_MAP[metric]
        domain_cfg = domain_thresholds.get(domain, {})
        threshold = _extract_threshold(domain_cfg, sub_key, key_map)
        stats = compute_stats(metric, domain, metric_rows, threshold)
        if stats is not None:
            result[domain].append(stats)

    return dict(result)
