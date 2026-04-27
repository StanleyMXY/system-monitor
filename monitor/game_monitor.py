# monitor/game_monitor.py
# 游戏供应商域监控采集器（P1）：按供应商维度采集转账失败率和对账差异
import time
from engine.models import MetricResult


def check_balance_transfer(conn, thresholds: dict) -> list[MetricResult]:
    """分供应商采集：余额转账失败率 / 重试异常订单数。"""
    cfg = thresholds["balance_transfer"]
    retry_threshold = cfg["retry_count_warning"]

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                manufacturer_id,
                manufacturer_name,
                COUNT(*)                                    AS total,
                SUM(status = 2)                             AS failed,
                SUM(retry_count >= %s)                      AS retry_anomaly_count
            FROM gam_balance_transfer
            WHERE created_at >= %s
            GROUP BY manufacturer_id, manufacturer_name
            """,
            (retry_threshold, int(time.time()) - 3600),
        )
        rows = cur.fetchall()

    results = []
    for row in rows:
        total = row["total"] or 0
        if total == 0:
            continue
        mid = row["manufacturer_id"]
        mname = row["manufacturer_name"] or f"供应商{mid}"
        extra = {"manufacturer_name": mname, "order_count": total}

        results.append(MetricResult(
            domain="game", metric="game_transfer_fail_rate",
            value=(row["failed"] or 0) / total,
            channel_id=mid, extra=extra,
        ))
        results.append(MetricResult(
            domain="game", metric="game_transfer_retry_count",
            value=float(row["retry_anomaly_count"] or 0),
            channel_id=mid, extra=extra,
        ))
    return results


def check_reconciliation(conn, thresholds: dict) -> list[MetricResult]:
    """采集各供应商连续对账差异天数。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                manufacturer_id,
                manufacturer_name,
                COUNT(*) AS consecutive_diff_days
            FROM sys_manuf_reconciliation_daily
            WHERE reconcile_status = 2
              AND stat_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
            GROUP BY manufacturer_id, manufacturer_name
            """
        )
        rows = cur.fetchall()

    return [
        MetricResult(
            domain="game", metric="game_reconciliation_diff_days",
            value=float(row["consecutive_diff_days"] or 0),
            channel_id=row["manufacturer_id"],
            extra={"manufacturer_name": row["manufacturer_name"] or f"供应商{row['manufacturer_id']}"},
        )
        for row in rows
    ]
