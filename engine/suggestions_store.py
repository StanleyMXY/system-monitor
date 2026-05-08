import json
import time
from config.db import get_monitor_conn


def insert_suggestions(rows: list[dict]) -> None:
    if not rows:
        return
    now = int(time.time() * 1000)
    conn = get_monitor_conn()
    try:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(
                    """
                    INSERT IGNORE INTO monitor_suggestions
                      (source, analysis_date, item_type, metric_key, payload, status, created_at)
                    VALUES (%s, %s, %s, %s, %s, 0, %s)
                    """,
                    (
                        row["source"],
                        row["analysis_date"],
                        row["item_type"],
                        row.get("metric_key"),
                        json.dumps(row["payload"], ensure_ascii=False),
                        now,
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def load_rejected_context(source: str, window_days: int) -> list[dict]:
    cutoff = int(time.time() * 1000) - window_days * 86400 * 1000
    conn = get_monitor_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source, item_type, metric_key, payload, reviewed_at
                FROM monitor_suggestions
                WHERE source = %s AND status = 2 AND reviewed_at >= %s
                ORDER BY reviewed_at DESC
                """,
                (source, cutoff),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    result = []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        result.append({
            "source": r["source"],
            "item_type": r["item_type"],
            "metric_key": r["metric_key"],
            "payload": payload,
            "reviewed_at": r["reviewed_at"],
        })
    return result
