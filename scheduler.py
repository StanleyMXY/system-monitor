# scheduler.py
# 常驻调度器：APScheduler interval/cron 驱动全域监控，集成实时看板
# 运行方式: python scheduler.py
import asyncio
import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.events import EVENT_JOB_ERROR

from config.db import get_source_conn, get_monitor_conn
from engine.threshold_config import load_thresholds
from engine.rule_engine import evaluate
from engine.alert_engine import handle
from executor.action_executor import enqueue_action
from dashboard.monitor_dashboard import MonitorDashboard
from monitor.payment_monitor import (
    check_recharge,
    check_channel_balance,
    check_withdraw_queue,
    check_withdraw_fail_rate,
)
from monitor.game_monitor import check_balance_transfer, check_reconciliation
from monitor.risk_monitor import (
    check_alert_backlog,
    check_alert_timeout,
    check_event_backlog,
    check_blacklist_expiry,
)
from monitor.activity_monitor import check_redemption, check_first_deposit
from monitor.account_monitor import check_frozen_balance
from monitor.operation_monitor import (
    check_vip_adjust,
    check_balance_adjustment,
    check_config_change,
)
from config.es import get_es_client
from monitor.log_monitor import (
    check_game_launch_error,
    check_mq_route_error,
    check_db_shard_error,
    check_websocket_error,
    check_db_duplicate_error,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

dashboard = MonitorDashboard(port=8080)


def _run_monitor(domain: str, check_fn):
    """执行单个采集函数的完整流程：采集 → 评估 → 告警 → 入队 → 更新看板。"""
    thresholds = load_thresholds(domain)
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
                        "value": float(result.metric.value),
                        "extra": result.metric.extra,
                    },
                    priority=1 if result.level == "critical" else 2,
                    triggered_by=result.message,
                    metric_value=result.metric.value,
                    threshold=result.threshold,
                )
        except Exception as exc:
            logger.error(f"处理告警结果异常 [{result.metric.metric}]: {exc}", exc_info=True)

    try:
        dashboard.update(domain, rule_results)
    except Exception as exc:
        logger.error(f"看板更新异常 [{domain}]: {exc}", exc_info=True)

    try:
        _write_metric_history(rule_results)
    except Exception as exc:
        logger.warning(f"历史指标写入失败（非致命）[{domain}]: {exc}")


def _run_log_monitor(check_fn):
    """执行日志采集函数的完整流程：采集 → 评估 → 告警 → 入队 → 更新看板。"""
    thresholds = load_thresholds("log")
    es = get_es_client()

    metrics = check_fn(es, thresholds)
    rule_results = evaluate(metrics, thresholds)

    for result in rule_results:
        if result.level == "ok":
            continue
        try:
            handle(result)
            if result.action == "enqueue":
                enqueue_action(
                    domain=result.metric.domain,
                    action_type="log_alert",
                    target_id=None,
                    payload={
                        "metric": result.metric.metric,
                        "value": float(result.metric.value),
                        "extra": result.metric.extra,
                    },
                    priority=1 if result.level == "critical" else 2,
                    triggered_by=result.message,
                    metric_value=result.metric.value,
                    threshold=result.threshold,
                )
        except Exception as exc:
            logger.error(f"处理日志告警结果异常 [{result.metric.metric}]: {exc}", exc_info=True)

    try:
        dashboard.update("log", rule_results)
    except Exception as exc:
        logger.error(f"看板更新异常 [log]: {exc}", exc_info=True)

    try:
        _write_metric_history(rule_results)
    except Exception as exc:
        logger.warning(f"历史指标写入失败（非致命）[log]: {exc}")


# --- payment jobs ---

async def job_check_recharge():
    logger.info("[payment] 执行充值监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_monitor, "payment", check_recharge)


async def job_check_channel_balance():
    logger.info("[payment] 执行渠道账号余额监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_monitor, "payment", check_channel_balance)


async def job_check_withdraw_queue():
    logger.info("[payment] 执行提现积压监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_monitor, "payment", check_withdraw_queue)


async def job_check_withdraw_fail_rate():
    logger.info("[payment] 执行提现失败率监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_monitor, "payment", check_withdraw_fail_rate)


# --- game jobs ---

async def job_check_balance_transfer():
    logger.info("[game] 执行游戏余额转账监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_monitor, "game", check_balance_transfer)


async def job_check_reconciliation():
    logger.info("[game] 执行厂商对账差异监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_monitor, "game", check_reconciliation)


# --- risk jobs ---

async def job_check_alert_backlog():
    logger.info("[risk] 执行高危预警积压监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_monitor, "risk", check_alert_backlog)


async def job_check_alert_timeout():
    logger.info("[risk] 执行高危预警超时监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_monitor, "risk", check_alert_timeout)


async def job_check_event_backlog():
    logger.info("[risk] 执行高危事件积压监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_monitor, "risk", check_event_backlog)


async def job_check_blacklist_expiry():
    logger.info("[risk] 执行临时黑名单到期监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_monitor, "risk", check_blacklist_expiry)


# --- activity jobs ---

async def job_check_redemption():
    logger.info("[activity] 执行活动兑换监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_monitor, "activity", check_redemption)


async def job_check_first_deposit():
    logger.info("[activity] 执行首充监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_monitor, "activity", check_first_deposit)


# --- account jobs ---

async def job_check_frozen_balance():
    logger.info("[account] 执行冻结余额监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_monitor, "account", check_frozen_balance)


# --- operation jobs ---

async def job_check_vip_adjust():
    logger.info("[operation] 执行 VIP 调整监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_monitor, "operation", check_vip_adjust)


async def job_check_balance_adjustment():
    logger.info("[operation] 执行大额余额调整监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_monitor, "operation", check_balance_adjustment)


async def job_check_config_change():
    logger.info("[operation] 执行配置变更监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_monitor, "operation", check_config_change)


# --- log jobs ---

async def job_check_game_launch_error():
    logger.info("[log] 执行游戏启动异常监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_log_monitor, check_game_launch_error)


async def job_check_mq_route_error():
    logger.info("[log] 执行 MQ 路由错误监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_log_monitor, check_mq_route_error)


async def job_check_db_shard_error():
    logger.info("[log] 执行分表路由错误监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_log_monitor, check_db_shard_error)


async def job_check_websocket_error():
    logger.info("[log] 执行 WebSocket 异常监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_log_monitor, check_websocket_error)


async def job_check_db_duplicate_error():
    logger.info("[log] 执行 DB 唯一键冲突监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_log_monitor, check_db_duplicate_error)


def _write_metric_history(rule_results: list) -> None:
    try:
        now = int(time.time() * 1000)
        rows = [
            (
                r.metric.domain,
                r.metric.metric,
                r.metric.channel_id,
                float(r.metric.value),
                r.level,
                now,
            )
            for r in rule_results
        ]
        conn = get_monitor_conn()
        try:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO monitor_metric_history
                      (domain, metric, channel_id, value, level, recorded_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    rows,
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning(f"历史指标写入失败（非致命）: {exc}")


async def job_cleanup_metric_history():
    """删除 90 天前的历史指标数据。"""
    logger.info("[cleanup] 清理过期历史指标")
    cutoff = int((time.time() - 90 * 86400) * 1000)
    conn = get_monitor_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM monitor_metric_history WHERE recorded_at < %s",
                (cutoff,),
            )
            deleted = cur.rowcount
        conn.commit()
        logger.info(f"[cleanup] 删除 {deleted} 条过期历史记录")
    except Exception as exc:
        logger.warning(f"历史记录清理失败（非致命）: {exc}")
    finally:
        conn.close()


def _on_job_error(event):
    logger.error(f"调度任务异常: {event.job_id} — {event.exception}")


def main():
    payment_cfg = load_thresholds("payment")
    game_cfg = load_thresholds("game")
    risk_cfg = load_thresholds("risk")
    activity_cfg = load_thresholds("activity")
    account_cfg = load_thresholds("account")
    operation_cfg = load_thresholds("operation")
    log_cfg = load_thresholds("log")

    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)

    # payment
    scheduler.add_job(job_check_recharge, "interval",
                      minutes=payment_cfg["recharge"]["check_interval_minutes"],
                      id="payment_recharge", max_instances=1)
    scheduler.add_job(job_check_channel_balance, "interval",
                      minutes=payment_cfg["channel_account"]["check_interval_minutes"],
                      id="payment_channel_balance", max_instances=1)
    scheduler.add_job(job_check_withdraw_queue, "interval",
                      minutes=payment_cfg["withdraw"]["check_interval_minutes"],
                      id="payment_withdraw_queue", max_instances=1)
    scheduler.add_job(job_check_withdraw_fail_rate, "interval",
                      minutes=payment_cfg["withdraw"]["fail_rate_interval_minutes"],
                      id="payment_withdraw_fail_rate", max_instances=1)

    # game
    scheduler.add_job(job_check_balance_transfer, "interval",
                      minutes=game_cfg["balance_transfer"]["check_interval_minutes"],
                      id="game_balance_transfer", max_instances=1)
    scheduler.add_job(job_check_reconciliation, "cron",
                      hour=2, minute=0,
                      id="game_reconciliation", max_instances=1)

    # risk
    scheduler.add_job(job_check_alert_backlog, "interval",
                      minutes=risk_cfg["alert"]["check_interval_minutes"],
                      id="risk_alert_backlog", max_instances=1)
    scheduler.add_job(job_check_alert_timeout, "interval",
                      minutes=30,
                      id="risk_alert_timeout", max_instances=1)
    scheduler.add_job(job_check_event_backlog, "interval",
                      minutes=risk_cfg["event"]["check_interval_minutes"],
                      id="risk_event_backlog", max_instances=1)
    scheduler.add_job(job_check_blacklist_expiry, "interval",
                      minutes=risk_cfg["blacklist"]["check_interval_minutes"],
                      id="risk_blacklist_expiry", max_instances=1)

    # activity
    scheduler.add_job(job_check_redemption, "interval",
                      minutes=activity_cfg["redemption"]["check_interval_minutes"],
                      id="activity_redemption", max_instances=1)
    scheduler.add_job(job_check_first_deposit, "interval",
                      minutes=activity_cfg["first_deposit"]["check_interval_minutes"],
                      id="activity_first_deposit", max_instances=1)

    # account
    scheduler.add_job(job_check_frozen_balance, "interval",
                      minutes=account_cfg["frozen_balance"]["check_interval_minutes"],
                      id="account_frozen_balance", max_instances=1)

    # operation
    scheduler.add_job(job_check_vip_adjust, "interval",
                      minutes=operation_cfg["vip_adjust"]["check_interval_minutes"],
                      id="operation_vip_adjust", max_instances=1)
    scheduler.add_job(job_check_balance_adjustment, "interval",
                      minutes=operation_cfg["balance_adjustment"]["check_interval_minutes"],
                      id="operation_balance_adjustment", max_instances=1)
    scheduler.add_job(job_check_config_change, "interval",
                      minutes=operation_cfg["config_change"]["check_interval_minutes"],
                      id="operation_config_change", max_instances=1)

    # log (high priority)
    scheduler.add_job(job_check_game_launch_error, "interval",
                      minutes=log_cfg["high_priority"]["check_interval_minutes"],
                      id="log_game_launch_error", max_instances=1)
    scheduler.add_job(job_check_mq_route_error, "interval",
                      minutes=log_cfg["high_priority"]["check_interval_minutes"],
                      id="log_mq_route_error", max_instances=1)
    scheduler.add_job(job_check_db_shard_error, "interval",
                      minutes=log_cfg["high_priority"]["check_interval_minutes"],
                      id="log_db_shard_error", max_instances=1)

    # log (low priority)
    scheduler.add_job(job_check_websocket_error, "interval",
                      minutes=log_cfg["low_priority"]["check_interval_minutes"],
                      id="log_websocket_error", max_instances=1)
    scheduler.add_job(job_check_db_duplicate_error, "interval",
                      minutes=log_cfg["low_priority"]["check_interval_minutes"],
                      id="log_db_duplicate_error", max_instances=1)

    # 每日历史清理（凌晨 03:00）
    scheduler.add_job(job_cleanup_metric_history, "cron",
                      hour=3, minute=0,
                      id="cleanup_metric_history", max_instances=1)

    dashboard.start()
    logger.info("监控看板已启动: http://localhost:8080/monitor_dashboard.html")

    async def _run():
        scheduler.start()
        logger.info("监控调度器已启动，Ctrl+C 退出")
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            scheduler.shutdown()
            logger.info("调度器已停止")

    try:
        asyncio.run(_run())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
