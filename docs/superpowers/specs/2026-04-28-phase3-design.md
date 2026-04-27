# Phase 3 设计文档：活动域 / 账户域 / 系统操作域

**日期：** 2026-04-28  
**状态：** 已批准，待实现

---

## 背景

Phase 1/2 已完成支付域、游戏供应商域、风控域的实时监控。Phase 3 在相同架构下新增三个域：活动域、用户账户域、系统操作域。

## 范围

### 不在本 Phase 内
- 流水要求完成率：时序累计指标，天然不适合实时阈值告警，由运营分析日报覆盖。
- AI 离线阈值优化器（Phase 4）。

---

## 1. 采集层（monitor/）

### monitor/activity_monitor.py

**`check_redemption(conn, thresholds) -> list[MetricResult]`**
- 数据源：`act_activity_redemption`
- 查询窗口：过去 `thresholds["redemption"]["check_interval_minutes"]` 分钟
- 返回两个 MetricResult：
  - `redemption_fail_rate`（domain="activity"）：失败订单 / 总订单
  - `redemption_fail_count`（domain="activity"）：失败订单绝对数量
  - `extra` 携带 `total_count`

**`check_first_deposit(conn, thresholds) -> list[MetricResult]`**
- 数据源：`act_first_deposit_record`
- 查询窗口：过去 `thresholds["first_deposit"]["check_interval_minutes"]` 分钟
- 返回两个 MetricResult：
  - `first_deposit_fail_count`：异常状态条目数
  - `first_deposit_volume`：首充量（用于量级骤降检测）
  - `extra` 携带 `is_business_hours: bool`（判断当前是否业务高峰时段，供规则函数消费）

### monitor/account_monitor.py

**`check_frozen_balance(conn, thresholds) -> list[MetricResult]`**
- 数据源：`usr_account`
- 查询逻辑：对比当前总冻结余额与 `thresholds["frozen_balance"]["check_interval_minutes"]`（60 分钟）前快照
- 返回：
  - `frozen_balance_growth_rate`（domain="account"）：(当前 - 历史) / 历史
  - `extra` 携带 `current_amount`、`previous_amount`

### monitor/operation_monitor.py

**`check_vip_adjust(conn, thresholds) -> list[MetricResult]`**
- 数据源：`adm_vip_adjust_log`
- 统计过去 1 小时操作次数
- 返回：`vip_adjust_count`（domain="operation"）

**`check_balance_adjustment(conn, thresholds) -> list[MetricResult]`**
- 数据源：`pay_balance_adjustment`
- 查询过去 `check_interval_minutes`（暂设 10 分钟）内单笔金额超 `large_amount_threshold` 的记录
- 返回：`balance_adjustment_large_count`（domain="operation"）
- `extra` 携带 `max_amount`

**`check_config_change(conn, thresholds) -> list[MetricResult]`**
- 数据源：`sys_operation_log`
- 按操作类型过滤配置变更类操作，统计过去 1 小时次数
- 返回：`config_change_count`（domain="operation"）

---

## 2. 规则层（engine/rule_engine.py）

新增 7 条规则，追加到 `_RULES` dict：

| 规则函数 | metric key | 触发条件 | level | action |
|---|---|---|---|---|
| `_activity_redemption_fail_rate` | `redemption_fail_rate` | > `fail_rate_warning`(0.05) | warning | alert |
| `_activity_redemption_fail_count` | `redemption_fail_count` | > `fail_count_warning`(10) | warning | alert |
| `_activity_first_deposit_fail_count` | `first_deposit_fail_count` | ≥ `fail_alert_threshold`(1) | warning | alert |
| `_activity_first_deposit_volume` | `first_deposit_volume` | = 0 且 `is_business_hours=True` | warning | alert |
| `_account_frozen_balance_growth` | `frozen_balance_growth_rate` | > `growth_rate_warning`(0.50) | warning | alert |
| `_operation_vip_adjust_count` | `vip_adjust_count` | > `batch_count_per_hour_warning`(20) | warning | enqueue |
| `_operation_balance_adjustment_large` | `balance_adjustment_large_count` | ≥ 1 | warning | enqueue |
| `_operation_config_change_count` | `config_change_count` | > `change_count_per_hour_warning`(10) | warning | alert |

`first_deposit_volume` 规则通过读取 `m.extra["is_business_hours"]` 判断是否触发，避免凌晨低峰误报。

---

## 3. 阈值配置（config/）

### thresholds_activity.json 补充键
```json
{
  "redemption": {
    "fail_count_warning": 10,
    ...
  },
  "first_deposit": {
    "volume_zero_check": true,
    ...
  }
}
```

### thresholds_operation.json 修正
- `balance_adjustment.check_interval_minutes` 当前为 `0`（疑似遗漏），修正为 `10`

---

## 4. 调度层（scheduler.py）

新增 6 个 job，延用 `_run_monitor(domain, check_fn)` 包装：

| job id | 采集函数 | 触发 | 间隔 |
|---|---|---|---|
| `activity_redemption` | `check_redemption` | interval | 15 分钟 |
| `activity_first_deposit` | `check_first_deposit` | interval | 5 分钟 |
| `account_frozen_balance` | `check_frozen_balance` | interval | 60 分钟 |
| `operation_vip_adjust` | `check_vip_adjust` | interval | 60 分钟 |
| `operation_balance_adjustment` | `check_balance_adjustment` | interval | 10 分钟 |
| `operation_config_change` | `check_config_change` | interval | 60 分钟 |

---

## 5. 看板（dashboard/monitor_dashboard.py）

`_DOMAIN_LABELS` 新增：
```python
"activity":  "活动域",
"account":   "账户域",
"operation": "系统操作域",
```

HTML grid 布局保持 `repeat(3, 1fr)`，6 个卡片自然排成 2 行 × 3 列。

---

## 架构约束（继承自 Phase 1/2）

- 读写分库：源库 `tian-gong`（只读），监控库 `tg_monitor`（读写）
- Monitor 函数接收 `conn + thresholds`，不自己开连接
- 调度器用 `asyncio.run()` + `AsyncIOScheduler`，job 函数 async，内部 `run_in_executor` 跑同步采集
- 实际表结构以测试库为准，与 SQL 有出入时按实际表修正
