import time
from engine.models import MetricResult


def check_vip_adjust(conn, thresholds: dict) -> list[MetricResult]:
    """采集过去 1 小时 VIP 调整操作次数。"""
    cutoff = int(time.time()) - 3600

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS adjust_count FROM adm_vip_adjust_log WHERE created_at >= %s",
            (cutoff,),
        )
        row = cur.fetchall()[0]

    return [MetricResult(
        domain="operation", metric="vip_adjust_count",
        value=float(row["adjust_count"] or 0),
    )]


def check_balance_adjustment(conn, thresholds: dict) -> list[MetricResult]:
    """采集过去 N 分钟内大额余额调整笔数。"""
    cfg = thresholds["balance_adjustment"]
    window = int(cfg["check_interval_minutes"] * 60)
    cutoff = int(time.time()) - window
    threshold_amount = cfg["large_amount_threshold"]

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS large_count, MAX(ABS(amount)) AS max_amount
            FROM pay_balance_adjustment
            WHERE created_at >= %s AND ABS(amount) >= %s
            """,
            (cutoff, threshold_amount),
        )
        row = cur.fetchall()[0]

    return [MetricResult(
        domain="operation", metric="balance_adjustment_large_count",
        value=float(row["large_count"] or 0),
        extra={"max_amount": float(row["max_amount"] or 0)},
    )]


def check_config_change(conn, thresholds: dict) -> list[MetricResult]:
    """采集过去 1 小时配置变更操作次数。"""
    cutoff = int(time.time()) - 3600

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS change_count
            FROM sys_operation_log
            WHERE operation_type = 'config_change' AND created_at >= %s
            """,
            (cutoff,),
        )
        row = cur.fetchall()[0]

    return [MetricResult(
        domain="operation", metric="config_change_count",
        value=float(row["change_count"] or 0),
    )]
