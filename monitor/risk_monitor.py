# monitor/risk_monitor.py
# 风控域监控采集器（P1）：高危预警积压 / 超时 / 风控事件积压 / 黑名单到期
import time
from engine.models import MetricResult


def check_alert_backlog(conn, thresholds: dict) -> list[MetricResult]:
    """采集高危风控预警积压数（alert_level>=3 且未处理）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS backlog_count FROM rsk_alert WHERE alert_level >= 3 AND handle_status = 0"
        )
        row = cur.fetchall()[0]

    return [MetricResult(
        domain="risk", metric="risk_alert_backlog_count",
        value=float(row["backlog_count"] or 0),
    )]


def check_alert_timeout(conn, thresholds: dict) -> list[MetricResult]:
    """采集高危预警超时未处理数（未处理且超过 high_risk_timeout_hours）。"""
    cfg = thresholds["alert"]
    cutoff = int(time.time()) - int(cfg["high_risk_timeout_hours"] * 3600)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS timeout_count
            FROM rsk_alert
            WHERE alert_level >= 3
              AND handle_status = 0
              AND created_at < %s
            """,
            (cutoff,),
        )
        row = cur.fetchall()[0]

    return [MetricResult(
        domain="risk", metric="risk_alert_timeout_count",
        value=float(row["timeout_count"] or 0),
    )]


def check_event_backlog(conn, thresholds: dict) -> list[MetricResult]:
    """采集高危风控事件积压数（event_level=3 且未处理）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS backlog_count FROM rsk_event WHERE event_level = 3 AND handle_status = 0"
        )
        row = cur.fetchall()[0]

    return [MetricResult(
        domain="risk", metric="risk_event_backlog_count",
        value=float(row["backlog_count"] or 0),
    )]


def check_blacklist_expiry(conn, thresholds: dict) -> list[MetricResult]:
    """采集临时黑名单即将到期数（expire_type=2 且在 expiry_reminder_hours 内到期）。"""
    cfg = thresholds["blacklist"]
    deadline = int(time.time()) + int(cfg["expiry_reminder_hours"] * 3600)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS expiry_count
            FROM rsk_blacklist
            WHERE expire_type = 2
              AND expire_time <= %s
              AND expire_time > %s
            """,
            (deadline, int(time.time())),
        )
        row = cur.fetchall()[0]

    return [MetricResult(
        domain="risk", metric="risk_blacklist_expiry_count",
        value=float(row["expiry_count"] or 0),
    )]
