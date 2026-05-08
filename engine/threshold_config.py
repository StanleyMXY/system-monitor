# engine/threshold_config.py
# 从 config/ 目录加载对应域的阈值 JSON，过滤元信息键
import json
from pathlib import Path

CONFIG_DIR = Path(__file__).parent.parent / "config"


def load_thresholds(domain: str) -> dict:
    path = CONFIG_DIR / f"thresholds_{domain}.json"
    with open(path, encoding="utf-8") as f:
        config = json.load(f)
    return {k: v for k, v in config.items() if not k.startswith("_")}


METRIC_THRESHOLD_MAP: dict[str, tuple[str, str, dict]] = {
    # payment
    "recharge_success_rate": ("payment", "recharge", {"warning": "success_rate_warning", "critical": "success_rate_critical"}),
    "recharge_timeout_rate": ("payment", "recharge", {"warning": "timeout_rate_warning"}),
    "recharge_pending_count": ("payment", "recharge", {"warning": "pending_count_warning"}),
    "channel_balance": ("payment", "channel_account", {"warning": "balance_warning_amount"}),
    "withdraw_queue_count": ("payment", "withdraw", {"warning": "queue_count_warning"}),
    "withdraw_fail_rate": ("payment", "withdraw", {"warning": "fail_rate_warning"}),
    # game
    "game_transfer_fail_rate": ("game", "balance_transfer", {"warning": "fail_rate_warning"}),
    "game_transfer_retry_count": ("game", "balance_transfer", {"warning": "retry_order_count_warning"}),
    "game_reconciliation_diff_days": ("game", "reconciliation", {"critical": "diff_consecutive_days_critical"}),
    # risk
    "risk_alert_backlog_count": ("risk", "alert", {"warning": "high_risk_pending_warning"}),
    "risk_alert_timeout_count": ("risk", "alert", {}),
    "risk_event_backlog_count": ("risk", "event", {"warning": "high_risk_pending_warning"}),
    "risk_blacklist_expiry_count": ("risk", "blacklist", {}),
    # activity
    "redemption_fail_rate": ("activity", "redemption", {"warning": "fail_rate_warning"}),
    "first_deposit_fail_count": ("activity", "first_deposit", {"warning": "fail_alert_threshold"}),
    # account
    "frozen_balance_growth_rate": ("account", "frozen_balance", {"warning": "growth_rate_warning"}),
    # operation
    "vip_adjust_count": ("operation", "vip_adjust", {"warning": "batch_count_per_hour_warning"}),
    "balance_adjustment_large_count": ("operation", "balance_adjustment", {}),
    "config_change_count": ("operation", "config_change", {"warning": "change_count_per_hour_warning"}),
}
