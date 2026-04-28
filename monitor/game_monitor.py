import time
from engine.models import MetricResult


def check_balance_transfer(conn, thresholds: dict) -> list[MetricResult]:
    """分供应商采集：余额转账失败率 / 重试异常订单数。"""
    cfg = thresholds["balance_transfer"]
    retry_threshold = cfg["retry_count_warning"]
    cutoff_ms = (int(time.time()) - 3600) * 1000  # created_at 为毫秒时间戳

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                vendor_id,
                provider_code,
                COUNT(*)                                    AS total,
                SUM(status = 2)                             AS failed,
                SUM(retry_count >= %s)                      AS retry_anomaly_count
            FROM gam_balance_transfer
            WHERE created_at >= %s
              AND is_deleted = 0
            GROUP BY vendor_id, provider_code
            """,
            (retry_threshold, cutoff_ms),
        )
        rows = cur.fetchall()

    results = []
    for row in rows:
        total = row["total"] or 0
        if total == 0:
            continue
        vendor_id = row["vendor_id"]
        vendor_name = row["provider_code"] or vendor_id
        extra = {"manufacturer_name": vendor_name, "order_count": total}

        results.append(MetricResult(
            domain="game", metric="game_transfer_fail_rate",
            value=(row["failed"] or 0) / total,
            extra=extra,
        ))
        results.append(MetricResult(
            domain="game", metric="game_transfer_retry_count",
            value=float(row["retry_anomaly_count"] or 0),
            extra=extra,
        ))
    return results


def check_reconciliation(conn, thresholds: dict) -> list[MetricResult]:
    """采集各供应商连续对账差异天数。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                manuf,
                COUNT(*) AS consecutive_diff_days
            FROM sys_manuf_reconciliation_daily
            WHERE reconcile_status = 2
              AND stat_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
              AND is_deleted = 0
            GROUP BY manuf
            """
        )
        rows = cur.fetchall()

    return [
        MetricResult(
            domain="game", metric="game_reconciliation_diff_days",
            value=float(row["consecutive_diff_days"] or 0),
            extra={"manufacturer_name": row["manuf"]},
        )
        for row in rows
    ]
