# engine/models.py
# 监控系统核心数据结构：Monitor 层输出 MetricResult，RuleEngine 输出 RuleResult
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class MetricResult:
    domain: str          # 监控域，如 "payment"
    metric: str          # 指标名，如 "recharge_success_rate"
    value: float         # 当前指标值
    channel_id: int | None = None  # 渠道 ID（无渠道维度时为 None）
    extra: dict = field(default_factory=dict)  # 附加上下文，如 channel_name、order_count


@dataclass
class RuleResult:
    level: Literal["ok", "warning", "critical"]
    action: Literal["none", "alert", "enqueue"]
    metric: MetricResult
    threshold: float
    message: str
