# engine/rule_engine.py
# 规则引擎：对 MetricResult 列表做阈值评估，输出 RuleResult 列表
# Monitor 层只采集数据，此处统一判断 level 和 action
from engine.models import MetricResult, RuleResult


def evaluate(results: list[MetricResult], thresholds: dict) -> list[RuleResult]:
    return [_evaluate_one(r, thresholds) for r in results]


def _evaluate_one(m: MetricResult, thresholds: dict) -> RuleResult:
    fn = _RULES.get(m.metric)
    if fn is None:
        return RuleResult(level="ok", action="none", metric=m, threshold=0.0, message="")
    return fn(m, thresholds)


def _recharge_success_rate(m: MetricResult, t: dict) -> RuleResult:
    cfg = t["recharge"]
    if m.value < cfg["success_rate_critical"]:
        return RuleResult(
            level="critical", action="enqueue", metric=m,
            threshold=cfg["success_rate_critical"],
            message=f"充值成功率严重低于阈值: {m.value:.1%} < {cfg['success_rate_critical']:.1%}"
                    + (f" [渠道 {m.channel_id}]" if m.channel_id else ""),
        )
    if m.value < cfg["success_rate_warning"]:
        return RuleResult(
            level="warning", action="alert", metric=m,
            threshold=cfg["success_rate_warning"],
            message=f"充值成功率低于预警线: {m.value:.1%} < {cfg['success_rate_warning']:.1%}"
                    + (f" [渠道 {m.channel_id}]" if m.channel_id else ""),
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=cfg["success_rate_warning"], message="")


def _recharge_timeout_rate(m: MetricResult, t: dict) -> RuleResult:
    cfg = t["recharge"]
    threshold = cfg["timeout_rate_warning"]
    if m.value > threshold:
        return RuleResult(
            level="warning", action="alert", metric=m, threshold=threshold,
            message=f"充值超时率偏高: {m.value:.1%} > {threshold:.1%}"
                    + (f" [渠道 {m.channel_id}]" if m.channel_id else ""),
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=threshold, message="")


def _recharge_pending_count(m: MetricResult, t: dict) -> RuleResult:
    cfg = t["recharge"]
    threshold = cfg["pending_count_warning"]
    if m.value > threshold:
        return RuleResult(
            level="warning", action="alert", metric=m, threshold=threshold,
            message=f"充值积压订单数超限: {int(m.value)} > {threshold}",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=threshold, message="")


def _channel_balance(m: MetricResult, t: dict) -> RuleResult:
    cfg = t["channel_account"]
    threshold = cfg["balance_warning_amount"]
    if m.value < threshold:
        name = m.extra.get("account_name", f"账号{m.channel_id}")
        return RuleResult(
            level="warning", action="alert", metric=m, threshold=threshold,
            message=f"渠道账号余额不足: {name} 余额 {m.value:.2f} < {threshold:.2f}",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=threshold, message="")


def _withdraw_queue_count(m: MetricResult, t: dict) -> RuleResult:
    cfg = t["withdraw"]
    threshold = cfg["queue_count_warning"]
    if m.value > threshold:
        return RuleResult(
            level="warning", action="alert", metric=m, threshold=threshold,
            message=f"提现审核积压超限: {int(m.value)} 条 > {threshold} 条",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=threshold, message="")


def _withdraw_fail_rate(m: MetricResult, t: dict) -> RuleResult:
    cfg = t["withdraw"]
    threshold = cfg["fail_rate_warning"]
    if m.value > threshold:
        return RuleResult(
            level="warning", action="alert", metric=m, threshold=threshold,
            message=f"提现失败率偏高: {m.value:.1%} > {threshold:.1%}",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=threshold, message="")


# --- game domain rules ---

def _game_transfer_fail_rate(m: MetricResult, t: dict) -> RuleResult:
    threshold = t["balance_transfer"]["fail_rate_warning"]
    if m.value > threshold:
        name = m.extra.get("manufacturer_name", f"供应商{m.channel_id}")
        return RuleResult(
            level="warning", action="alert", metric=m, threshold=threshold,
            message=f"游戏转账失败率偏高: {name} {m.value:.1%} > {threshold:.1%}",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=threshold, message="")


def _game_transfer_retry_count(m: MetricResult, t: dict) -> RuleResult:
    threshold = t["balance_transfer"]["retry_order_count_warning"]
    if m.value > threshold:
        name = m.extra.get("manufacturer_name", f"供应商{m.channel_id}")
        return RuleResult(
            level="warning", action="alert", metric=m, threshold=threshold,
            message=f"游戏转账重试异常: {name} {int(m.value)} 笔 > {threshold} 笔",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=threshold, message="")


def _game_reconciliation_diff_days(m: MetricResult, t: dict) -> RuleResult:
    threshold = t["reconciliation"]["diff_consecutive_days_critical"]
    if m.value >= threshold:
        name = m.extra.get("manufacturer_name", f"供应商{m.channel_id}")
        return RuleResult(
            level="critical", action="enqueue", metric=m, threshold=threshold,
            message=f"厂商对账连续差异: {name} 连续 {int(m.value)} 天存在差异",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=threshold, message="")


# --- risk domain rules ---

def _risk_alert_backlog_count(m: MetricResult, t: dict) -> RuleResult:
    threshold = t["alert"]["high_risk_pending_warning"]
    if m.value > threshold:
        return RuleResult(
            level="warning", action="alert", metric=m, threshold=threshold,
            message=f"高危预警积压超限: {int(m.value)} 条 > {threshold} 条",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=threshold, message="")


def _risk_alert_timeout_count(m: MetricResult, t: dict) -> RuleResult:
    if m.value > 0:
        return RuleResult(
            level="warning", action="alert", metric=m, threshold=0,
            message=f"高危预警超时未处理: {int(m.value)} 条",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=0, message="")


def _risk_event_backlog_count(m: MetricResult, t: dict) -> RuleResult:
    threshold = t["event"]["high_risk_pending_warning"]
    if m.value > threshold:
        return RuleResult(
            level="warning", action="alert", metric=m, threshold=threshold,
            message=f"高危风控事件积压超限: {int(m.value)} 条 > {threshold} 条",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=threshold, message="")


def _risk_blacklist_expiry_count(m: MetricResult, t: dict) -> RuleResult:
    if m.value > 0:
        return RuleResult(
            level="warning", action="alert", metric=m, threshold=0,
            message=f"临时黑名单即将到期: {int(m.value)} 条需人工复审",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=0, message="")


# --- activity domain rules ---

def _activity_redemption_fail_rate(m: MetricResult, t: dict) -> RuleResult:
    threshold = t["redemption"]["fail_rate_warning"]
    if m.value > threshold:
        return RuleResult(
            level="warning", action="alert", metric=m, threshold=threshold,
            message=f"活动兑换失败率偏高: {m.value:.1%} > {threshold:.1%}"
                    f"（共 {m.extra.get('total_count', '?')} 笔）",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=threshold, message="")


def _activity_first_deposit_fail_count(m: MetricResult, t: dict) -> RuleResult:
    threshold = t["first_deposit"]["fail_alert_threshold"]
    if m.value >= threshold:
        return RuleResult(
            level="warning", action="alert", metric=m, threshold=threshold,
            message=f"首充异常记录: {int(m.value)} 条",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=threshold, message="")


# --- account domain rules ---

def _account_frozen_balance_growth(m: MetricResult, t: dict) -> RuleResult:
    threshold = t["frozen_balance"]["growth_rate_warning"]
    if m.value > threshold:
        curr = m.extra.get("current_amount", 0)
        prev = m.extra.get("previous_amount", 0)
        return RuleResult(
            level="warning", action="alert", metric=m, threshold=threshold,
            message=f"冻结余额增长率异常: {m.value:.1%} > {threshold:.1%}"
                    f"（{prev:.0f} → {curr:.0f}）",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=threshold, message="")


# --- operation domain rules ---

def _operation_vip_adjust_count(m: MetricResult, t: dict) -> RuleResult:
    threshold = t["vip_adjust"]["batch_count_per_hour_warning"]
    if m.value > threshold:
        return RuleResult(
            level="warning", action="enqueue", metric=m, threshold=threshold,
            message=f"VIP 批量调整异常: 近1小时 {int(m.value)} 次 > {threshold} 次",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=threshold, message="")


def _operation_balance_adjustment_large(m: MetricResult, t: dict) -> RuleResult:
    if m.value >= 1:
        max_amt = m.extra.get("max_amount", 0)
        threshold_amt = t["balance_adjustment"]["large_amount_threshold"]
        return RuleResult(
            level="warning", action="enqueue", metric=m, threshold=1.0,
            message=f"大额余额调整: {int(m.value)} 笔超过 {threshold_amt} 元（最大 {max_amt:.0f} 元）",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=1.0, message="")


def _operation_config_change_count(m: MetricResult, t: dict) -> RuleResult:
    threshold = t["config_change"]["change_count_per_hour_warning"]
    if m.value > threshold:
        return RuleResult(
            level="warning", action="alert", metric=m, threshold=threshold,
            message=f"系统配置变更频繁: 近1小时 {int(m.value)} 次 > {threshold} 次",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=threshold, message="")


# --- log domain rules ---

def _log_game_launch_error(m: MetricResult, t: dict) -> RuleResult:
    cfg = t["high_priority"]["game_launch_error"]
    if m.value >= cfg["count_critical"]:
        sample = m.extra.get("sample", "")
        return RuleResult(
            level="critical", action="enqueue", metric=m,
            threshold=cfg["count_critical"],
            message=f"游戏启动严重异常: {int(m.value)} 次 >= {cfg['count_critical']} 次"
                    + (f" | {sample}" if sample else ""),
        )
    if m.value >= cfg["count_warning"]:
        return RuleResult(
            level="warning", action="alert", metric=m,
            threshold=cfg["count_warning"],
            message=f"游戏启动异常偏高: {int(m.value)} 次 >= {cfg['count_warning']} 次",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=cfg["count_warning"], message="")


def _log_mq_route_error(m: MetricResult, t: dict) -> RuleResult:
    cfg = t["high_priority"]["mq_route_error"]
    if m.value >= cfg["count_critical"]:
        return RuleResult(
            level="critical", action="enqueue", metric=m,
            threshold=cfg["count_critical"],
            message=f"MQ 路由严重故障: {int(m.value)} 次 >= {cfg['count_critical']} 次",
        )
    if m.value >= cfg["count_warning"]:
        return RuleResult(
            level="warning", action="alert", metric=m,
            threshold=cfg["count_warning"],
            message=f"MQ 路由失败偏高: {int(m.value)} 次 >= {cfg['count_warning']} 次",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=cfg["count_warning"], message="")


def _log_db_shard_error(m: MetricResult, t: dict) -> RuleResult:
    cfg = t["high_priority"]["db_shard_error"]
    if m.value >= cfg["count_critical"]:
        return RuleResult(
            level="critical", action="enqueue", metric=m,
            threshold=cfg["count_critical"],
            message=f"分表路由严重故障: {int(m.value)} 次 >= {cfg['count_critical']} 次",
        )
    if m.value >= cfg["count_warning"]:
        return RuleResult(
            level="warning", action="alert", metric=m,
            threshold=cfg["count_warning"],
            message=f"分表路由失败: {int(m.value)} 次 >= {cfg['count_warning']} 次",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=cfg["count_warning"], message="")


def _log_websocket_error(m: MetricResult, t: dict) -> RuleResult:
    threshold = t["low_priority"]["websocket_error"]["count_warning"]
    if m.value >= threshold:
        return RuleResult(
            level="warning", action="alert", metric=m, threshold=threshold,
            message=f"WebSocket 异常偏高: {int(m.value)} 次 >= {threshold} 次",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=threshold, message="")


def _log_db_duplicate_error(m: MetricResult, t: dict) -> RuleResult:
    threshold = t["low_priority"]["db_duplicate_error"]["count_warning"]
    if m.value >= threshold:
        return RuleResult(
            level="warning", action="alert", metric=m, threshold=threshold,
            message=f"DB 唯一键冲突偏高: {int(m.value)} 次 >= {threshold} 次",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=threshold, message="")


_RULES = {
    "recharge_success_rate": _recharge_success_rate,
    "recharge_timeout_rate": _recharge_timeout_rate,
    "recharge_pending_count": _recharge_pending_count,
    "channel_balance": _channel_balance,
    "withdraw_queue_count": _withdraw_queue_count,
    "withdraw_fail_rate": _withdraw_fail_rate,
    # game domain
    "game_transfer_fail_rate": _game_transfer_fail_rate,
    "game_transfer_retry_count": _game_transfer_retry_count,
    "game_reconciliation_diff_days": _game_reconciliation_diff_days,
    # risk domain
    "risk_alert_backlog_count": _risk_alert_backlog_count,
    "risk_alert_timeout_count": _risk_alert_timeout_count,
    "risk_event_backlog_count": _risk_event_backlog_count,
    "risk_blacklist_expiry_count": _risk_blacklist_expiry_count,
    # activity domain
    "redemption_fail_rate": _activity_redemption_fail_rate,
    "first_deposit_fail_count": _activity_first_deposit_fail_count,
    # account domain
    "frozen_balance_growth_rate": _account_frozen_balance_growth,
    # operation domain
    "vip_adjust_count": _operation_vip_adjust_count,
    "balance_adjustment_large_count": _operation_balance_adjustment_large,
    "config_change_count": _operation_config_change_count,
    # log domain
    "game_launch_error_count": _log_game_launch_error,
    "mq_route_error_count": _log_mq_route_error,
    "db_shard_error_count": _log_db_shard_error,
    "websocket_error_count": _log_websocket_error,
    "db_duplicate_error_count": _log_db_duplicate_error,
}
