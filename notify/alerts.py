import logging
import os

from dotenv import load_dotenv

from notify.tg_client import send_alert

load_dotenv()

logger = logging.getLogger(__name__)


def notify_critical_alert(message: str, correlation) -> None:
    """实时 critical 告警推送，带根因信息（known/suspected）。由 alert_engine 调用。"""
    if os.getenv("TG_NOTIFY_ENABLED", "false").lower() != "true":
        return

    text = f"🚨 <b>Critical 告警</b>\n{message}"

    if correlation and correlation.confidence in ("known", "suspected"):
        conf_label = "已知根因" if correlation.confidence == "known" else "疑似根因"
        cause = f"{correlation.cause_domain}.{correlation.cause_metric}"
        lead = f" 于 {int(correlation.lead_minutes)} 分钟前异常" if correlation.lead_minutes else ""
        text += f"\n{conf_label}：<code>{cause}</code>{lead}"

    send_alert(text)
    logger.debug(f"[alerts] 已发送 critical 告警通知")


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
