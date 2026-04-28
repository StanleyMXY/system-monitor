import time
from engine.models import MetricResult


def check_frozen_balance(conn, thresholds: dict) -> list[MetricResult]:
    """采集冻结余额增长率（当前 vs N 分钟前）。"""
    cfg = thresholds["frozen_balance"]
    window = int(cfg["check_interval_minutes"] * 60)
    cutoff = int(time.time()) - window

    with conn.cursor() as cur:
        cur.execute("SELECT SUM(frozen_balance) AS current_frozen FROM usr_account")
        current_row = cur.fetchall()[0]

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT SUM(frozen_balance) AS prev_frozen
            FROM usr_account
            WHERE updated_at <= %s
            """,
            (cutoff,),
        )
        prev_row = cur.fetchall()[0]

    current = float(current_row["current_frozen"] or 0)
    previous = float(prev_row["prev_frozen"] or 0)
    rate = (current - previous) / previous if previous > 0 else 0.0

    return [
        MetricResult(
            domain="account", metric="frozen_balance_growth_rate",
            value=rate,
            extra={"current_amount": current, "previous_amount": previous},
        )
    ]
