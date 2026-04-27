# 天工平台系统运营监控 — Phase 2 设计文档

日期：2026-04-27

---

## 1. Phase 2 范围

在 Phase 1（支付域 P0）基础上，新增：

- `monitor/game_monitor.py` — 游戏供应商域监控（P1）
- `monitor/risk_monitor.py` — 风控域监控（P1）
- `dashboard/monitor_dashboard.py` — 实时监控看板（HTTP server + HTML）
- 扩展 `engine/rule_engine.py` — 新增游戏域和风控域规则
- 扩展 `scheduler.py` — 注册新 job

---

## 2. 架构（延续 Phase 1）

数据流完全一致，新增两个 monitor 模块：

```
game_monitor / risk_monitor（采集）→ list[MetricResult]
    → rule_engine.evaluate()        → list[RuleResult]
        → alert_engine.handle()     → logs/alerts.log
        → dashboard.update()        → output/monitor_dashboard.html
        → action_executor.enqueue() → monitor_action_queue（仅 critical）
```

新增目录：
```
dashboard/
  __init__.py
  monitor_dashboard.py    # HTTP server + HTML 生成
output/                   # 生成的 HTML 文件
  monitor_dashboard.html
```

---

## 3. game_monitor.py

数据源：`gam_balance_transfer`、`sys_manuf_reconciliation_daily`

按供应商维度（20-30 个），每次产出若干 MetricResult。

### 函数

| 函数 | 数据源 | 产出指标 | 频率 |
|---|---|---|---|
| `check_balance_transfer(conn, thresholds)` | `gam_balance_transfer` | 每供应商：fail_rate / retry_anomaly_count | 10分钟 |
| `check_reconciliation(conn, thresholds)` | `sys_manuf_reconciliation_daily` | 每供应商：reconciliation_diff_days | 每日 cron |

### 指标规则

| metric | 告警条件 | action |
|---|---|---|
| `game_transfer_fail_rate` | > `balance_transfer.fail_rate_warning` | alert |
| `game_transfer_retry_count` | > `balance_transfer.retry_order_count_warning` | alert |
| `game_reconciliation_diff_days` | >= `reconciliation.diff_consecutive_days_critical` | enqueue |

---

## 4. risk_monitor.py

数据源：`rsk_alert`、`rsk_event`、`rsk_blacklist`

### 函数

| 函数 | 数据源 | 产出指标 | 频率 |
|---|---|---|---|
| `check_alert_backlog(conn, thresholds)` | `rsk_alert` | risk_alert_backlog_count | 10分钟 |
| `check_alert_timeout(conn, thresholds)` | `rsk_alert` | risk_alert_timeout_count | 30分钟 |
| `check_event_backlog(conn, thresholds)` | `rsk_event` | risk_event_backlog_count | 10分钟 |
| `check_blacklist_expiry(conn, thresholds)` | `rsk_blacklist` | risk_blacklist_expiry_count | 每小时 |

### 指标规则

| metric | 告警条件 | action |
|---|---|---|
| `risk_alert_backlog_count` | > `alert.high_risk_pending_warning` | alert |
| `risk_alert_timeout_count` | > 0 | alert |
| `risk_event_backlog_count` | > `event.high_risk_pending_warning` | alert |
| `risk_blacklist_expiry_count` | > 0 | alert |

---

## 5. monitor_dashboard.py

### 设计

- **布局**：顶部三域汇总卡片横排 + 下方告警流（最新 20 条，倒序）
- **主题**：暗色，与现有日报风格一致
- **刷新**：`<meta http-equiv="refresh" content="30">`
- **HTTP server**：`http.server` 在后台线程运行，默认端口 `8080`，serve `output/monitor_dashboard.html`

### 接口

```python
class MonitorDashboard:
    def __init__(self, port: int = 8080): ...
    def start(self) -> None:          # 启动后台 HTTP 线程
    def update(self, domain: str, results: list[RuleResult]) -> None:
        # 更新内存状态，重新生成 HTML 文件
```

### 内存状态结构

```python
# 每域最新一轮的 RuleResult 列表
domain_results: dict[str, list[RuleResult]]

# 最近 50 条非 ok 告警（deque）
recent_alerts: deque[RuleResult]
```

### HTML 结构

```
标题栏：天工平台 系统监控 | 最后更新时间
域汇总卡片（3列）：
  每卡片：域名 | critical 数 | warning 数 | 边框颜色反映最高级别
告警列表（最新 20 条）：
  每行：[级别] 消息文字
```

---

## 6. 规则引擎扩展

在 `engine/rule_engine.py` 的 `_RULES` dict 追加 7 个新规则函数（游戏 3 个 + 风控 4 个），不改变 `evaluate()` 接口。

---

## 7. 调度器扩展

`scheduler.py` 新增 job：

| job id | 函数 | 间隔 | 配置键 |
|---|---|---|---|
| `game_balance_transfer` | `check_balance_transfer` | 10分钟 | `balance_transfer.check_interval_minutes` |
| `game_reconciliation` | `check_reconciliation` | cron 每日 02:00 | 固定 |
| `risk_alert_backlog` | `check_alert_backlog` | 10分钟 | `alert.check_interval_minutes` |
| `risk_alert_timeout` | `check_alert_timeout` | 30分钟 | 固定 |
| `risk_event_backlog` | `check_event_backlog` | 10分钟 | `event.check_interval_minutes` |
| `risk_blacklist_expiry` | `check_blacklist_expiry` | 60分钟 | `blacklist.check_interval_minutes` |

dashboard.update() 在 scheduler 的每个 job 完成后调用（alert_engine.handle 之后）。

---

## 8. Phase 2 范围边界

**包含：**
- `monitor/game_monitor.py`（2 个函数）
- `monitor/risk_monitor.py`（4 个函数）
- `dashboard/monitor_dashboard.py`（HTTP server + HTML）
- `dashboard/__init__.py`
- `output/` 目录
- rule_engine.py 新增 7 条规则
- scheduler.py 新增 6 个 job + dashboard 集成
- 各模块对应单元测试

**不包含（Phase 3+）：**
- 活动域 / 用户账户域 / 系统操作域 monitor
- Telegram 告警通知
- AI 阈值优化器
