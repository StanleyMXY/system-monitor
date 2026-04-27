# 天工平台系统运营监控 — Phase 1 设计文档

日期：2026-04-25

---

## 1. 项目定位

天工平台（tian-gong）业务分析系统的衍生独立系统，专注于平台基础设施的实时运营监控。Phase 1 实现支付域 P0 监控、配置文件体系、规则引擎和基础告警。

---

## 2. 数据库架构

**读写分库：**

| 连接函数 | 库 | 用途 |
|---|---|---|
| `get_source_conn()` | `tian-gong`（只读） | SELECT 监控采集 |
| `get_monitor_conn()` | `tg_monitor`（新建） | 写 `monitor_action_queue`、告警日志 |

两组连接参数通过 `.env` 配置，`config/db.py` 统一管理，不在其他模块硬编码。分库原因：`tian-gong` 是生产库，避免监控写操作增加其压力；告警实时性不受影响（读和写是独立操作，写队列额外延迟 < 5ms）。

---

## 3. 目录结构

```
tian-gong-monitor/
├── config/
│   ├── db.py
│   ├── thresholds_payment.json
│   ├── thresholds_game.json
│   ├── thresholds_risk.json
│   ├── thresholds_activity.json
│   ├── thresholds_account.json
│   └── thresholds_operation.json
├── engine/
│   ├── threshold_config.py
│   ├── rule_engine.py
│   └── alert_engine.py
├── monitor/
│   └── payment_monitor.py
├── executor/
│   └── action_executor.py
├── logs/
├── .env
├── .env.example
└── scheduler.py
```

---

## 4. 数据流

```
payment_monitor（SQL 采集）→ list[MetricResult]
    → rule_engine.evaluate()  → list[RuleResult]
        → alert_engine.handle()    → logs/alerts.log（所有 warning+）
        → action_executor.enqueue() → monitor_action_queue（仅 critical）
```

---

## 5. 核心数据结构

```python
@dataclass
class MetricResult:
    domain: str           # "payment"
    metric: str           # "recharge_success_rate"
    channel_id: int | None
    value: float
    extra: dict           # channel_name, order_count 等上下文

@dataclass
class RuleResult:
    level: str            # "ok" | "warning" | "critical"
    action: str           # "none" | "alert" | "enqueue"
    metric: MetricResult
    threshold: float
    message: str
```

---

## 6. 各模块职责

### `config/db.py`
- `get_source_conn()` → pymysql 连接 tian-gong（只读）
- `get_monitor_conn()` → pymysql 连接 tg_monitor（读写）
- 连接参数从 `.env` 读取，风格与现有系统 `db.py` 一致

### `engine/threshold_config.py`
- `load_thresholds(domain: str) → dict`
- 过滤 `_comment` 等元信息键，返回纯配置 dict

### `monitor/payment_monitor.py`
4 个异步采集函数，各返回 `list[MetricResult]`：

| 函数 | 数据源 | 产出指标 |
|---|---|---|
| `check_recharge()` | pay_recharge + pay_channel | 每渠道：成功率 / 超时率 / 积压数 |
| `check_channel_balance()` | pay_channel_account | 每账号：余额 |
| `check_withdraw_queue()` | pay_withdraw | 积压数 |
| `check_withdraw_fail_rate()` | pay_withdraw | 失败率 |

### `engine/rule_engine.py`
- `evaluate(results: list[MetricResult]) → list[RuleResult]`
- 从 `threshold_config` 读阈值，按域+指标名匹配规则
- 输出 level（ok/warning/critical）和 action（none/alert/enqueue）

### `engine/alert_engine.py`
- Phase 1：打印结构化日志 + 写 `logs/alerts.log`
- 接口为 `handle(result: RuleResult)`，后续接入 Telegram 只改此模块

### `executor/action_executor.py`
- `enqueue_action(domain, action_type, target_id, payload, priority, triggered_by, metric_value, threshold)`
- INSERT 到 `tg_monitor.monitor_action_queue`
- `monitor_action_queue` 建表 SQL 见方案文档第五章

### `scheduler.py`
- `AsyncIOScheduler(timezone="Asia/Shanghai")`，风格与现有系统一致
- interval 从各域 JSON 的 `check_interval_minutes` 读取
- Phase 1 注册 4 个 payment monitor job

---

## 7. 调度时间表（Phase 1）

| 函数 | 间隔 | 配置键 |
|---|---|---|
| `check_recharge` | 5 分钟 | `recharge.check_interval_minutes` |
| `check_channel_balance` | 15 分钟 | `channel_account.check_interval_minutes` |
| `check_withdraw_queue` | 15 分钟 | `withdraw.check_interval_minutes` |
| `check_withdraw_fail_rate` | 60 分钟 | `withdraw.fail_rate_interval_minutes`（新增键，默认 60） |

---

## 8. 建表 SQL（tg_monitor 库）

```sql
CREATE DATABASE IF NOT EXISTS tg_monitor DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE tg_monitor;

CREATE TABLE monitor_action_queue (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  domain       VARCHAR(32)   NOT NULL COMMENT '监控域',
  action_type  VARCHAR(64)   NOT NULL COMMENT '动作类型',
  target_id    VARCHAR(64)   NULL,
  payload      JSON          NOT NULL,
  status       TINYINT       NOT NULL DEFAULT 0 COMMENT '0-待审批 1-已批准 2-已执行 3-已拒绝',
  priority     TINYINT       NOT NULL DEFAULT 2 COMMENT '1-紧急 2-普通 3-低',
  triggered_by VARCHAR(128)  NOT NULL,
  metric_value DECIMAL(18,4) NULL,
  threshold    DECIMAL(18,4) NULL,
  operator     VARCHAR(64)   NULL,
  approved_at  BIGINT        NULL,
  executed_at  BIGINT        NULL,
  created_at   BIGINT        NOT NULL,
  updated_at   BIGINT        NOT NULL
);
```

---

## 9. Phase 1 范围边界

**包含：**
- 全部 6 个 JSON 阈值配置文件（含默认值）
- `config/db.py`、`engine/threshold_config.py`
- `monitor/payment_monitor.py`（4 个采集函数）
- `engine/rule_engine.py`、`engine/alert_engine.py`（日志+文件告警）
- `executor/action_executor.py`（写 monitor_action_queue）
- `scheduler.py`（4 个 payment job）
- `.env.example`、`logs/` 目录

**不包含（Phase 2+）：**
- 其他五个域的 monitor（game/risk/activity/account/operation）
- Telegram 告警通知
- AI 阈值优化器
- 监控看板 HTML
