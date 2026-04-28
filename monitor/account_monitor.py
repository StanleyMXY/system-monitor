import time
from engine.models import MetricResult

# 上一轮采集的冻结余额总量，用于与当前值对比计算增长率
_prev_frozen: float | None = None


def check_frozen_balance(conn, thresholds: dict) -> list[MetricResult]:
    """采集冻结余额增长率（当前值 vs 上一轮采集值）。"""
    global _prev_frozen

    with conn.cursor() as cur:
        cur.execute("SELECT SUM(frozen_balance) AS current_frozen FROM usr_account")
        row = cur.fetchall()[0]

    current = float(row["current_frozen"] or 0)

    if _prev_frozen is None:
        _prev_frozen = current
        return [MetricResult(
            domain="account", metric="frozen_balance_growth_rate",
            value=0.0,
            extra={"current_amount": current, "previous_amount": current},
        )]

    previous = _prev_frozen
    rate = (current - previous) / previous if previous > 0 else 0.0
    _prev_frozen = current

    return [MetricResult(
        domain="account", metric="frozen_balance_growth_rate",
        value=rate,
        extra={"current_amount": current, "previous_amount": previous},
    )]
