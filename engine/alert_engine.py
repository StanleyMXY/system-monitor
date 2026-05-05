import json
import logging
import time
from decimal import Decimal
from pathlib import Path

from engine.models import RuleResult
from engine.correlation_engine import correlate, CorrelationResult

logger = logging.getLogger(__name__)

ALERT_LOG_PATH = Path(__file__).parent.parent / "logs" / "alerts.log"


class _SafeEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


def handle(result: RuleResult) -> None:
    if result.level == "ok":
        return

    try:
        cr: CorrelationResult = correlate(result)
    except Exception as exc:
        logger.warning(f"correlation_engine 异常，跳过关联分析: {exc}")
        cr = CorrelationResult(
            confidence="none", cause_domain=None, cause_metric=None,
            cause_ts=None, lead_minutes=None, message="",
        )

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

    if cr.confidence != "none":
        entry["correlation"] = {
            "confidence": cr.confidence,
            "cause_domain": cr.cause_domain,
            "cause_metric": cr.cause_metric,
            "cause_ts": cr.cause_ts,
            "lead_minutes": cr.lead_minutes,
            "message": cr.message,
        }

    if result.level == "warning":
        logger.warning(result.message)
    elif result.level == "critical":
        logger.critical(result.message)

    ALERT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ALERT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, cls=_SafeEncoder) + "\n")
