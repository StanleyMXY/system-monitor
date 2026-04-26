# scheduler.py
# 常驻调度器：APScheduler interval 驱动支付域监控全流程
# 运行方式: python scheduler.py
import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.events import EVENT_JOB_ERROR

from config.db import get_source_conn
from engine.threshold_config import load_thresholds
from engine.rule_engine import evaluate
from engine.alert_engine import handle
from executor.action_executor import enqueue_action
from monitor.payment_monitor import (
    check_recharge,
    check_channel_balance,
    check_withdraw_queue,
    check_withdraw_fail_rate,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _run_monitor(check_fn):
    """执行单个采集函数的完整流程：采集 → 评估 → 告警 → 入队。"""
    thresholds = load_thresholds("payment")
    conn = get_source_conn()
    try:
        metrics = check_fn(conn, thresholds)
    finally:
        conn.close()

    rule_results = evaluate(metrics, thresholds)

    for result in rule_results:
        if result.level == "ok":
            continue
        try:
            handle(result)
            if result.action == "enqueue":
                enqueue_action(
                    domain=result.metric.domain,
                    action_type="switch_channel",
                    target_id=str(result.metric.channel_id) if result.metric.channel_id else None,
                    payload={
                        "metric": result.metric.metric,
                        "value": result.metric.value,
                        "extra": result.metric.extra,
                    },
                    priority=1 if result.level == "critical" else 2,
                    triggered_by=result.message,
                    metric_value=result.metric.value,
                    threshold=result.threshold,
                )
        except Exception as exc:
            logger.error(f"处理告警结果异常 [{result.metric.metric}]: {exc}", exc_info=True)


async def job_check_recharge():
    logger.info("[payment] 执行充值监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_monitor, check_recharge)


async def job_check_channel_balance():
    logger.info("[payment] 执行渠道账号余额监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_monitor, check_channel_balance)


async def job_check_withdraw_queue():
    logger.info("[payment] 执行提现积压监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_monitor, check_withdraw_queue)


async def job_check_withdraw_fail_rate():
    logger.info("[payment] 执行提现失败率监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_monitor, check_withdraw_fail_rate)


def _on_job_error(event):
    logger.error(f"调度任务异常: {event.job_id} — {event.exception}")


def main():
    payment_cfg = load_thresholds("payment")

    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)

    scheduler.add_job(
        job_check_recharge,
        "interval",
        minutes=payment_cfg["recharge"]["check_interval_minutes"],
        id="payment_recharge",
        max_instances=1,
    )
    scheduler.add_job(
        job_check_channel_balance,
        "interval",
        minutes=payment_cfg["channel_account"]["check_interval_minutes"],
        id="payment_channel_balance",
        max_instances=1,
    )
    scheduler.add_job(
        job_check_withdraw_queue,
        "interval",
        minutes=payment_cfg["withdraw"]["check_interval_minutes"],
        id="payment_withdraw_queue",
        max_instances=1,
    )
    scheduler.add_job(
        job_check_withdraw_fail_rate,
        "interval",
        minutes=payment_cfg["withdraw"]["fail_rate_interval_minutes"],
        id="payment_withdraw_fail_rate",
        max_instances=1,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    scheduler.start()
    logger.info("监控调度器已启动，Ctrl+C 退出")
    try:
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logger.info("调度器已停止")


if __name__ == "__main__":
    main()
