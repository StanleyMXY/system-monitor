# monitor/payment_monitor.py
# 支付域监控采集器（P0）：查询 tian-gong 库，返回 MetricResult 列表
# 不直接开连接，由调度器传入，便于测试和连接管理
import time
from engine.models import MetricResult


def check_recharge(conn, thresholds: dict) -> list[MetricResult]:
    """分渠道采集：充值成功率 / 超时率 / 积压数。"""
    cfg = thresholds["recharge"]
    cutoff = int(time.time()) - cfg["pending_timeout_minutes"] * 60

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                pc.id                                        AS channel_id,
                pc.name                                      AS channel_name,
                COUNT(*)                                     AS total,
                SUM(pr.status = 1)                          AS success,
                SUM(pr.status = 3)                          AS timeout,
                SUM(pr.status = 0 AND pr.created_at < %s)  AS pending_overdue
            FROM pay_recharge pr
            JOIN pay_channel pc ON pc.id = pr.channel_id
            WHERE pr.created_at >= %s
            GROUP BY pc.id, pc.name
            """,
            (cutoff, int(time.time()) - 3600),
        )
        rows = cur.fetchall()

    results = []
    for row in rows:
        total = row["total"] or 0
        if total == 0:
            continue
        ch_id = row["channel_id"]
        ch_name = row["channel_name"]
        extra = {"channel_name": ch_name, "order_count": total}

        results.append(MetricResult(
            domain="payment", metric="recharge_success_rate",
            value=(row["success"] or 0) / total,
            channel_id=ch_id, extra=extra,
        ))
        results.append(MetricResult(
            domain="payment", metric="recharge_timeout_rate",
            value=(row["timeout"] or 0) / total,
            channel_id=ch_id, extra=extra,
        ))
        results.append(MetricResult(
            domain="payment", metric="recharge_pending_count",
            value=float(row["pending_overdue"] or 0),
            channel_id=ch_id, extra=extra,
        ))
    return results


def check_channel_balance(conn, thresholds: dict) -> list[MetricResult]:
    """采集所有渠道账号余额。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id AS account_id, name AS account_name, balance FROM pay_channel_account"
        )
        rows = cur.fetchall()

    return [
        MetricResult(
            domain="payment", metric="channel_balance",
            value=float(row["balance"]),
            channel_id=row["account_id"],
            extra={"account_name": row["account_name"]},
        )
        for row in rows
    ]


def check_withdraw_queue(conn, thresholds: dict) -> list[MetricResult]:
    """采集提现审核积压数（状态=0 且超 queue_timeout_hours）。"""
    cfg = thresholds["withdraw"]
    cutoff = int(time.time()) - int(cfg["queue_timeout_hours"] * 3600)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS overdue_count FROM pay_withdraw WHERE status = 0 AND created_at < %s",
            (cutoff,),
        )
        row = cur.fetchall()[0]

    return [MetricResult(
        domain="payment", metric="withdraw_queue_count",
        value=float(row["overdue_count"]),
    )]


def check_withdraw_fail_rate(conn, thresholds: dict) -> list[MetricResult]:
    """采集最近 1 小时提现失败率（status=4 / 已处理总数）。"""
    since = int(time.time()) - 3600

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                COUNT(*) AS total_processed,
                SUM(status = 4) AS failed
            FROM pay_withdraw
            WHERE status IN (3, 4) AND updated_at >= %s
            """,
            (since,),
        )
        row = cur.fetchall()[0]

    total = row["total_processed"] or 0
    if total == 0:
        return []

    return [MetricResult(
        domain="payment", metric="withdraw_fail_rate",
        value=(row["failed"] or 0) / total,
        extra={"sample_count": total},
    )]
