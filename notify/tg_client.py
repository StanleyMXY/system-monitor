import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_TIMEOUT = 5


def _alert_chat_ids() -> list[str]:
    raw = os.getenv("TG_ALERT_CHAT_IDS", "")
    return [cid.strip() for cid in raw.split(",") if cid.strip()]


def send_alert(text: str) -> None:
    """发送系统告警到 TG_ALERT_CHAT_IDS，失败只记日志不抛出。"""
    token = os.getenv("TG_BOT_TOKEN", "")
    chat_ids = _alert_chat_ids()
    if not token or not chat_ids:
        logger.warning("[tg_client] TG_BOT_TOKEN 或 TG_ALERT_CHAT_IDS 未配置，跳过通知")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chat_id in chat_ids:
        try:
            resp = requests.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
            }, timeout=_TIMEOUT)
            data = resp.json()
            if not data.get("ok"):
                logger.warning(f"[tg_client] sendMessage 失败 (chat_id={chat_id}): {data.get('description')}")
        except Exception as exc:
            logger.warning(f"[tg_client] sendMessage 异常 (chat_id={chat_id}): {exc}")
