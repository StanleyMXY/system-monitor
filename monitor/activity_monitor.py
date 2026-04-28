import time
from datetime import datetime
from engine.models import MetricResult


def _is_business_hours() -> bool:
    """业务高峰时段判断：10:00-23:00。"""
    hour = datetime.now().hour
    return 10 <= hour <= 23


def check_redemption(conn, thresholds: dict) -> list[MetricResult]:
    """采集活动兑换失败率和失败数量。"""
    cfg = thresholds["redemption"]
    window = int(cfg["check_interval_minutes"] * 60)
    cutoff = int(time.time()) - window

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status != 1 THEN 1 ELSE 0 END) AS fail
            FROM act_activity_redemption
            WHERE created_at >= %s
            """,
            (cutoff,),
        )
        row = cur.fetchall()[0]

    total = int(row["total"] or 0)
    fail = int(row["fail"] or 0)
    rate = fail / total if total > 0 else 0.0

    return [
        MetricResult(
            domain="activity", metric="redemption_fail_rate",
            value=rate,
            extra={"total_count": total, "fail_count": fail},
        ),
        MetricResult(
            domain="activity", metric="redemption_fail_count",
            value=float(fail),
            extra={"total_count": total},
        ),
    ]


def check_first_deposit(conn, thresholds: dict) -> list[MetricResult]:
    """采集首充异常条目数和首充量。"""
    cfg = thresholds["first_deposit"]
    window = int(cfg["check_interval_minutes"] * 60)
    cutoff = int(time.time()) - window

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS total_count,
                   SUM(CASE WHEN status != 1 THEN 1 ELSE 0 END) AS fail_count
            FROM act_first_deposit_record
            WHERE created_at >= %s
            """,
            (cutoff,),
        )
        row = cur.fetchall()[0]

    total = int(row["total_count"] or 0)
    fail = int(row["fail_count"] or 0)

    return [
        MetricResult(
            domain="activity", metric="first_deposit_fail_count",
            value=float(fail),
        ),
        MetricResult(
            domain="activity", metric="first_deposit_volume",
            value=float(total),
            extra={"is_business_hours": _is_business_hours()},
        ),
    ]
