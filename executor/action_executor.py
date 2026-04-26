# executor/action_executor.py
# 执行层：将需人工审批或高危动作写入 tg_monitor.monitor_action_queue
import json
import time
from config.db import get_monitor_conn


def enqueue_action(
    domain: str,
    action_type: str,
    target_id: str | None,
    payload: dict,
    priority: int,
    triggered_by: str,
    metric_value: float | None,
    threshold: float | None,
) -> None:
    now = int(time.time())
    conn = get_monitor_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO monitor_action_queue
                  (domain, action_type, target_id, payload, status, priority,
                   triggered_by, metric_value, threshold, created_at, updated_at)
                VALUES (%s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s)
                """,
                (
                    domain,
                    action_type,
                    target_id,
                    json.dumps(payload, ensure_ascii=False),
                    priority,
                    triggered_by,
                    metric_value,
                    threshold,
                    now,
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()
