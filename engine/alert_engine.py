# engine/alert_engine.py
# 告警引擎：Phase 1 实现结构化日志输出 + 写 logs/alerts.log
# Phase 2 接入 Telegram 时只扩展此模块，接口不变
import json
import logging
import time
from pathlib import Path
from engine.models import RuleResult

logger = logging.getLogger(__name__)

ALERT_LOG_PATH = Path(__file__).parent.parent / "logs" / "alerts.log"


def handle(result: RuleResult) -> None:
    if result.level == "ok":
        return

    entry = {
        "ts": int(time.time()),
        "level": result.level,
        "domain": result.metric.domain,
        "metric": result.metric.metric,
        "value": result.metric.value,
        "threshold": result.threshold,
        "channel_id": result.metric.channel_id,
        "message": result.message,
    }

    if result.level == "warning":
        logger.warning(result.message)
    elif result.level == "critical":
        logger.critical(result.message)

    ALERT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ALERT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
