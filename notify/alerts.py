import logging
import os

from dotenv import load_dotenv

from notify.tg_client import send_alert

load_dotenv()

logger = logging.getLogger(__name__)


def notify_watchdog_alert(last_beat_at: str, elapsed_minutes: int) -> None:
    """调度器心跳超期告警，由 scripts/watchdog.py 调用。"""
    if os.getenv("TG_NOTIFY_ENABLED", "false").lower() != "true":
        logger.warning(f"[watchdog] 心跳超期 {elapsed_minutes} 分钟（最后心跳: {last_beat_at}），TG 通知未启用")
        return

    text = (
        f"🚨 <b>监控调度器无响应</b>\n"
        f"最后心跳: <code>{last_beat_at}</code>\n"
        f"已超期 <b>{elapsed_minutes} 分钟</b>，请检查 scheduler.py 进程"
    )
    send_alert(text)
    logger.warning(f"[watchdog] 已发送心跳超期告警，elapsed={elapsed_minutes}m")
