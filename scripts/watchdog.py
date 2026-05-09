"""
调度器心跳看门狗。

通过系统 cron 每 10 分钟执行一次：
  */10 * * * * cd /path/to/project && python -m scripts.watchdog >> logs/watchdog.log 2>&1

检查 scheduler_heartbeat.last_beat_at 是否超过 10 分钟未更新，
超期则通过 TG 发送告警。
"""

import logging
import sys
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("watchdog")

STALE_MINUTES = 10


def main() -> None:
    from config.db import get_monitor_conn
    from notify.alerts import notify_watchdog_alert

    conn = get_monitor_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT last_beat_at FROM scheduler_heartbeat WHERE id = 1")
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        logger.warning("[watchdog] scheduler_heartbeat 表无记录，调度器可能从未启动")
        notify_watchdog_alert("（从未写入）", STALE_MINUTES)
        sys.exit(1)

    last_beat: datetime = row["last_beat_at"]
    # pymysql 返回 naive datetime，视为服务器本地时区
    now = datetime.now()
    elapsed = int((now - last_beat).total_seconds() / 60)

    if elapsed >= STALE_MINUTES:
        logger.warning(f"[watchdog] 心跳超期 {elapsed} 分钟，最后心跳: {last_beat}")
        notify_watchdog_alert(str(last_beat), elapsed)
        sys.exit(1)
    else:
        logger.info(f"[watchdog] 心跳正常，距上次 {elapsed} 分钟")


if __name__ == "__main__":
    main()
