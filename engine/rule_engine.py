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


_RULES = {
    "recharge_success_rate": _recharge_success_rate,
    "recharge_timeout_rate": _recharge_timeout_rate,
    "recharge_pending_count": _recharge_pending_count,
    "channel_balance": _channel_balance,
    "withdraw_queue_count": _withdraw_queue_count,
    "withdraw_fail_rate": _withdraw_fail_rate,
}
