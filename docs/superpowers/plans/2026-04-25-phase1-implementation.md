# 天工平台系统运营监控 Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现天工平台系统运营监控 Phase 1：支付域 P0 监控 + 配置文件体系 + 规则引擎 + 基础告警 + 审批队列写入 + 调度器。

**Architecture:** 读写分库（`tian-gong` 只读采集，`tg_monitor` 写入告警队列）。Monitor 层只做 SQL 查询并返回 `MetricResult`，`rule_engine` 统一做阈值评估输出 `RuleResult`，`alert_engine` 处理日志/文件告警，`action_executor` 写入 `monitor_action_queue`，`scheduler` 用 APScheduler interval 驱动全流程。

**Tech Stack:** Python 3.10+, PyMySQL, APScheduler, python-dotenv, pytest

---

## 文件清单

| 操作 | 路径 | 职责 |
|---|---|---|
| Create | `.env.example` | 环境变量模板 |
| Create | `config/db.py` | 两个 DB 连接函数 |
| Create | `config/thresholds_payment.json` | 支付域阈值默认值 |
| Create | `config/thresholds_game.json` | 游戏域阈值默认值 |
| Create | `config/thresholds_risk.json` | 风控域阈值默认值 |
| Create | `config/thresholds_activity.json` | 活动域阈值默认值 |
| Create | `config/thresholds_account.json` | 用户账户域阈值默认值 |
| Create | `config/thresholds_operation.json` | 系统操作域阈值默认值 |
| Create | `engine/threshold_config.py` | 阈值 JSON 加载器 |
| Create | `engine/models.py` | MetricResult / RuleResult dataclass |
| Create | `engine/rule_engine.py` | 阈值评估逻辑 |
| Create | `engine/alert_engine.py` | 日志 + 文件告警 |
| Create | `monitor/payment_monitor.py` | 4 个支付域采集函数 |
| Create | `executor/action_executor.py` | 写 monitor_action_queue |
| Create | `scheduler.py` | APScheduler 调度器主入口 |
| Create | `db/init_monitor.sql` | tg_monitor 库和 monitor_action_queue 建表 |
| Create | `logs/.gitkeep` | 保留 logs 目录 |
| Create | `tests/test_rule_engine.py` | rule_engine 单元测试 |
| Create | `tests/test_threshold_config.py` | threshold_config 单元测试 |
| Create | `tests/test_alert_engine.py` | alert_engine 单元测试 |
| Create | `tests/test_action_executor.py` | action_executor 单元测试（mock DB） |
| Create | `tests/test_payment_monitor.py` | payment_monitor 单元测试（mock DB） |

---

## Task 1: 项目骨架与环境配置

**Files:**
- Create: `.env.example`
- Create: `logs/.gitkeep`
- Create: `config/__init__.py`
- Create: `engine/__init__.py`
- Create: `monitor/__init__.py`
- Create: `executor/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: 创建目录结构**

```bash
cd "D:/Projects/PycharmProjects/System Monitor"
mkdir -p config engine monitor executor executor logs db tests
touch config/__init__.py engine/__init__.py monitor/__init__.py executor/__init__.py tests/__init__.py logs/.gitkeep
```

- [ ] **Step 2: 创建 `.env.example`**

```ini
# 数据源：tian-gong 生产库（只读）
SOURCE_HOST=127.0.0.1
SOURCE_PORT=3306
SOURCE_USER=readonly_user
SOURCE_PASSWORD=your_password
SOURCE_DATABASE=tian-gong

# 监控库：tg_monitor（读写）
MONITOR_HOST=127.0.0.1
MONITOR_PORT=3306
MONITOR_USER=monitor_user
MONITOR_PASSWORD=your_password
MONITOR_DATABASE=tg_monitor
```

- [ ] **Step 3: 复制 `.env.example` 为 `.env` 并填入真实配置**

```bash
cp .env.example .env
# 编辑 .env，填入真实数据库连接信息
```

- [ ] **Step 4: 确认 venv 中安装了依赖**

```bash
.venv/Scripts/pip install pymysql apscheduler python-dotenv pytest
```

Expected: 全部安装成功，无报错。

- [ ] **Step 5: Commit**

```bash
git init
git add .env.example config/__init__.py engine/__init__.py monitor/__init__.py executor/__init__.py tests/__init__.py logs/.gitkeep
git commit -m "chore: init project skeleton"
```

---

## Task 2: 数据库连接层（config/db.py）

**Files:**
- Create: `config/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_db.py`：

```python
import importlib
import sys
import os


def test_get_source_conn_uses_source_env(monkeypatch):
    monkeypatch.setenv("SOURCE_HOST", "src-host")
    monkeypatch.setenv("SOURCE_PORT", "3307")
    monkeypatch.setenv("SOURCE_USER", "src_user")
    monkeypatch.setenv("SOURCE_PASSWORD", "src_pass")
    monkeypatch.setenv("SOURCE_DATABASE", "src_db")
    monkeypatch.setenv("MONITOR_HOST", "mon-host")
    monkeypatch.setenv("MONITOR_PORT", "3306")
    monkeypatch.setenv("MONITOR_USER", "mon_user")
    monkeypatch.setenv("MONITOR_PASSWORD", "mon_pass")
    monkeypatch.setenv("MONITOR_DATABASE", "mon_db")

    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return object()

    import pymysql
    monkeypatch.setattr(pymysql, "connect", fake_connect)

    if "config.db" in sys.modules:
        del sys.modules["config.db"]

    from config import db
    db.get_source_conn()

    assert captured["host"] == "src-host"
    assert captured["port"] == 3307
    assert captured["user"] == "src_user"
    assert captured["database"] == "src_db"


def test_get_monitor_conn_uses_monitor_env(monkeypatch):
    monkeypatch.setenv("SOURCE_HOST", "src-host")
    monkeypatch.setenv("SOURCE_PORT", "3306")
    monkeypatch.setenv("SOURCE_USER", "src_user")
    monkeypatch.setenv("SOURCE_PASSWORD", "src_pass")
    monkeypatch.setenv("SOURCE_DATABASE", "src_db")
    monkeypatch.setenv("MONITOR_HOST", "mon-host")
    monkeypatch.setenv("MONITOR_PORT", "3308")
    monkeypatch.setenv("MONITOR_USER", "mon_user")
    monkeypatch.setenv("MONITOR_PASSWORD", "mon_pass")
    monkeypatch.setenv("MONITOR_DATABASE", "mon_db")

    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return object()

    import pymysql
    monkeypatch.setattr(pymysql, "connect", fake_connect)

    if "config.db" in sys.modules:
        del sys.modules["config.db"]

    from config import db
    db.get_monitor_conn()

    assert captured["host"] == "mon-host"
    assert captured["port"] == 3308
    assert captured["database"] == "mon_db"
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
.venv/Scripts/pytest tests/test_db.py -v
```

Expected: `ImportError` 或 `ModuleNotFoundError`（config/db.py 尚未创建）。

- [ ] **Step 3: 实现 `config/db.py`**

```python
# config/db.py
# 数据库连接工厂：两个独立连接，tian-gong 只读采集，tg_monitor 监控写入
import os
import pymysql
from dotenv import load_dotenv

load_dotenv()


def get_source_conn() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=os.getenv("SOURCE_HOST", "127.0.0.1"),
        port=int(os.getenv("SOURCE_PORT", "3306")),
        user=os.getenv("SOURCE_USER"),
        password=os.getenv("SOURCE_PASSWORD"),
        database=os.getenv("SOURCE_DATABASE"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def get_monitor_conn() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=os.getenv("MONITOR_HOST", "127.0.0.1"),
        port=int(os.getenv("MONITOR_PORT", "3306")),
        user=os.getenv("MONITOR_USER"),
        password=os.getenv("MONITOR_PASSWORD"),
        database=os.getenv("MONITOR_DATABASE"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
.venv/Scripts/pytest tests/test_db.py -v
```

Expected: 2 passed。

- [ ] **Step 5: Commit**

```bash
git add config/db.py tests/test_db.py
git commit -m "feat: add db connection factory (source + monitor)"
```

---

## Task 3: 阈值 JSON 配置文件（6 个）

**Files:**
- Create: `config/thresholds_payment.json`
- Create: `config/thresholds_game.json`
- Create: `config/thresholds_risk.json`
- Create: `config/thresholds_activity.json`
- Create: `config/thresholds_account.json`
- Create: `config/thresholds_operation.json`

- [ ] **Step 1: 创建 `config/thresholds_payment.json`**

```json
{
  "_comment": "金额单位: 元，时间单位: 分钟，比率单位: 0-1",
  "recharge": {
    "success_rate_warning": 0.80,
    "success_rate_critical": 0.60,
    "timeout_rate_warning": 0.10,
    "pending_timeout_minutes": 30,
    "pending_count_warning": 20,
    "check_interval_minutes": 5
  },
  "withdraw": {
    "queue_timeout_hours": 4,
    "queue_count_warning": 50,
    "fail_rate_warning": 0.20,
    "large_amount_threshold": 50000,
    "check_interval_minutes": 15,
    "fail_rate_interval_minutes": 60
  },
  "channel_account": {
    "balance_warning_amount": 10000,
    "check_interval_minutes": 15
  }
}
```

- [ ] **Step 2: 创建 `config/thresholds_game.json`**

```json
{
  "_comment": "比率单位: 0-1，金额单位: 元，时间单位: 分钟",
  "balance_transfer": {
    "fail_rate_warning": 0.05,
    "retry_count_warning": 3,
    "retry_order_count_warning": 5,
    "check_interval_minutes": 10
  },
  "reconciliation": {
    "diff_consecutive_days_critical": 2,
    "diff_amount_warning": 100
  }
}
```

- [ ] **Step 3: 创建 `config/thresholds_risk.json`**

```json
{
  "_comment": "时间单位: 小时，数量单位: 条",
  "alert": {
    "high_risk_pending_warning": 10,
    "high_risk_timeout_hours": 2,
    "check_interval_minutes": 10
  },
  "event": {
    "high_risk_pending_warning": 5,
    "check_interval_minutes": 10
  },
  "blacklist": {
    "expiry_reminder_hours": 24,
    "check_interval_minutes": 60
  }
}
```

- [ ] **Step 4: 创建 `config/thresholds_activity.json`**

```json
{
  "_comment": "比率单位: 0-1，数量单位: 条，时间单位: 分钟",
  "redemption": {
    "fail_rate_warning": 0.05,
    "retry_count_warning": 3,
    "retry_order_count_warning": 10,
    "check_interval_minutes": 15
  },
  "first_deposit": {
    "fail_alert_threshold": 1,
    "check_interval_minutes": 5
  }
}
```

- [ ] **Step 5: 创建 `config/thresholds_account.json`**

```json
{
  "_comment": "比率单位: 0-1，时间单位: 小时",
  "flow_requirement": {
    "expiry_hours": 24,
    "low_completion_rate": 0.10,
    "count_warning": 20,
    "check_interval_minutes": 60
  },
  "frozen_balance": {
    "growth_rate_warning": 0.50,
    "check_interval_minutes": 60
  }
}
```

- [ ] **Step 6: 创建 `config/thresholds_operation.json`**

```json
{
  "_comment": "时间单位: 小时，金额单位: 元，数量单位: 次",
  "vip_adjust": {
    "batch_count_per_hour_warning": 20,
    "check_interval_minutes": 60
  },
  "balance_adjustment": {
    "large_amount_threshold": 10000,
    "check_interval_minutes": 0
  },
  "config_change": {
    "change_count_per_hour_warning": 10,
    "check_interval_minutes": 60
  }
}
```

- [ ] **Step 7: Commit**

```bash
git add config/thresholds_*.json
git commit -m "feat: add threshold config files for all 6 domains"
```

---

## Task 4: 阈值配置加载器（engine/threshold_config.py）

**Files:**
- Create: `engine/threshold_config.py`
- Create: `tests/test_threshold_config.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_threshold_config.py`：

```python
import json
import pytest
from pathlib import Path


def test_load_thresholds_returns_dict_without_comment_keys(tmp_path, monkeypatch):
    cfg = {
        "_comment": "should be filtered",
        "recharge": {"success_rate_warning": 0.80}
    }
    cfg_file = tmp_path / "thresholds_payment.json"
    cfg_file.write_text(json.dumps(cfg), encoding="utf-8")

    import engine.threshold_config as tc
    monkeypatch.setattr(tc, "CONFIG_DIR", tmp_path)

    result = tc.load_thresholds("payment")
    assert "_comment" not in result
    assert result["recharge"]["success_rate_warning"] == 0.80


def test_load_thresholds_raises_on_missing_file(tmp_path, monkeypatch):
    import engine.threshold_config as tc
    monkeypatch.setattr(tc, "CONFIG_DIR", tmp_path)

    with pytest.raises(FileNotFoundError):
        tc.load_thresholds("nonexistent")
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
.venv/Scripts/pytest tests/test_threshold_config.py -v
```

Expected: `ImportError` 或 `ModuleNotFoundError`。

- [ ] **Step 3: 实现 `engine/threshold_config.py`**

```python
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
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
.venv/Scripts/pytest tests/test_threshold_config.py -v
```

Expected: 2 passed。

- [ ] **Step 5: Commit**

```bash
git add engine/threshold_config.py tests/test_threshold_config.py
git commit -m "feat: add threshold config loader"
```

---

## Task 5: 核心数据模型（engine/models.py）

**Files:**
- Create: `engine/models.py`

- [ ] **Step 1: 创建 `engine/models.py`**

```python
# engine/models.py
# 监控系统核心数据结构：Monitor 层输出 MetricResult，RuleEngine 输出 RuleResult
from dataclasses import dataclass, field


@dataclass
class MetricResult:
    domain: str          # 监控域，如 "payment"
    metric: str          # 指标名，如 "recharge_success_rate"
    value: float         # 当前指标值
    channel_id: int | None = None  # 渠道 ID（无渠道维度时为 None）
    extra: dict = field(default_factory=dict)  # 附加上下文，如 channel_name、order_count


@dataclass
class RuleResult:
    level: str           # "ok" | "warning" | "critical"
    action: str          # "none" | "alert" | "enqueue"
    metric: MetricResult
    threshold: float     # 触发告警的阈值
    message: str         # 人可读的告警描述
```

- [ ] **Step 2: 验证模型可正常导入**

```bash
.venv/Scripts/python -c "from engine.models import MetricResult, RuleResult; print('OK')"
```

Expected: 打印 `OK`，无报错。

- [ ] **Step 3: Commit**

```bash
git add engine/models.py
git commit -m "feat: add MetricResult and RuleResult dataclasses"
```

---

## Task 6: 规则引擎（engine/rule_engine.py）

**Files:**
- Create: `engine/rule_engine.py`
- Create: `tests/test_rule_engine.py`

规则引擎接收 `list[MetricResult]`，按指标名从阈值配置中查找对应规则，输出 `list[RuleResult]`。Phase 1 实现支付域的 5 个指标规则：

| metric | warning 条件 | critical 条件 | critical action |
|---|---|---|---|
| `recharge_success_rate` | value < `recharge.success_rate_warning` | value < `recharge.success_rate_critical` | enqueue |
| `recharge_timeout_rate` | value > `recharge.timeout_rate_warning` | — | alert |
| `recharge_pending_count` | value > `recharge.pending_count_warning` | — | alert |
| `channel_balance` | value < `channel_account.balance_warning_amount` | — | alert |
| `withdraw_queue_count` | value > `withdraw.queue_count_warning` | — | alert |
| `withdraw_fail_rate` | value > `withdraw.fail_rate_warning` | — | alert |

- [ ] **Step 1: 写失败测试**

新建 `tests/test_rule_engine.py`：

```python
import pytest
from engine.models import MetricResult
from engine.rule_engine import evaluate


PAYMENT_THRESHOLDS = {
    "recharge": {
        "success_rate_warning": 0.80,
        "success_rate_critical": 0.60,
        "timeout_rate_warning": 0.10,
        "pending_count_warning": 20,
    },
    "withdraw": {
        "queue_count_warning": 50,
        "fail_rate_warning": 0.20,
    },
    "channel_account": {
        "balance_warning_amount": 10000,
    },
}


def make_metric(metric, value, channel_id=None, extra=None):
    return MetricResult(
        domain="payment",
        metric=metric,
        value=value,
        channel_id=channel_id,
        extra=extra or {},
    )


def test_recharge_success_rate_ok():
    results = evaluate([make_metric("recharge_success_rate", 0.95)], PAYMENT_THRESHOLDS)
    assert len(results) == 1
    assert results[0].level == "ok"
    assert results[0].action == "none"


def test_recharge_success_rate_warning():
    results = evaluate([make_metric("recharge_success_rate", 0.75)], PAYMENT_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"
    assert results[0].threshold == 0.80


def test_recharge_success_rate_critical():
    results = evaluate([make_metric("recharge_success_rate", 0.55)], PAYMENT_THRESHOLDS)
    assert results[0].level == "critical"
    assert results[0].action == "enqueue"
    assert results[0].threshold == 0.60


def test_recharge_timeout_rate_warning():
    results = evaluate([make_metric("recharge_timeout_rate", 0.15)], PAYMENT_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"


def test_recharge_timeout_rate_ok():
    results = evaluate([make_metric("recharge_timeout_rate", 0.05)], PAYMENT_THRESHOLDS)
    assert results[0].level == "ok"


def test_recharge_pending_count_warning():
    results = evaluate([make_metric("recharge_pending_count", 25)], PAYMENT_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"


def test_channel_balance_warning():
    results = evaluate([make_metric("channel_balance", 5000)], PAYMENT_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"
    assert results[0].threshold == 10000


def test_withdraw_queue_count_warning():
    results = evaluate([make_metric("withdraw_queue_count", 60)], PAYMENT_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"


def test_withdraw_fail_rate_ok():
    results = evaluate([make_metric("withdraw_fail_rate", 0.10)], PAYMENT_THRESHOLDS)
    assert results[0].level == "ok"


def test_withdraw_fail_rate_warning():
    results = evaluate([make_metric("withdraw_fail_rate", 0.25)], PAYMENT_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"


def test_unknown_metric_returns_ok():
    results = evaluate([make_metric("unknown_metric", 999)], PAYMENT_THRESHOLDS)
    assert results[0].level == "ok"
    assert results[0].action == "none"


def test_multiple_metrics():
    metrics = [
        make_metric("recharge_success_rate", 0.55),
        make_metric("channel_balance", 5000),
    ]
    results = evaluate(metrics, PAYMENT_THRESHOLDS)
    assert len(results) == 2
    levels = {r.metric.metric: r.level for r in results}
    assert levels["recharge_success_rate"] == "critical"
    assert levels["channel_balance"] == "warning"
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
.venv/Scripts/pytest tests/test_rule_engine.py -v
```

Expected: `ImportError`。

- [ ] **Step 3: 实现 `engine/rule_engine.py`**

```python
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
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
.venv/Scripts/pytest tests/test_rule_engine.py -v
```

Expected: 11 passed。

- [ ] **Step 5: Commit**

```bash
git add engine/rule_engine.py tests/test_rule_engine.py
git commit -m "feat: add rule engine for payment domain (6 metrics)"
```

---

## Task 7: 告警引擎（engine/alert_engine.py）

**Files:**
- Create: `engine/alert_engine.py`
- Create: `tests/test_alert_engine.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_alert_engine.py`：

```python
import json
import logging
from pathlib import Path
from engine.models import MetricResult, RuleResult
from engine.alert_engine import handle


def make_rule_result(level, action, metric_name="recharge_success_rate", value=0.5, threshold=0.8):
    metric = MetricResult(domain="payment", metric=metric_name, value=value)
    return RuleResult(level=level, action=action, metric=metric, threshold=threshold,
                      message=f"测试告警: {metric_name} = {value}")


def test_handle_ok_does_nothing(caplog):
    result = make_rule_result("ok", "none")
    with caplog.at_level(logging.DEBUG):
        handle(result)
    assert "告警" not in caplog.text


def test_handle_warning_logs(caplog):
    result = make_rule_result("warning", "alert")
    with caplog.at_level(logging.WARNING):
        handle(result)
    assert "WARNING" in caplog.text or "warning" in caplog.text.lower()


def test_handle_critical_logs(caplog):
    result = make_rule_result("critical", "enqueue")
    with caplog.at_level(logging.CRITICAL):
        handle(result)
    assert "CRITICAL" in caplog.text or "critical" in caplog.text.lower()


def test_handle_warning_writes_to_file(tmp_path, monkeypatch):
    import engine.alert_engine as ae
    log_file = tmp_path / "alerts.log"
    monkeypatch.setattr(ae, "ALERT_LOG_PATH", log_file)

    result = make_rule_result("warning", "alert", value=0.75, threshold=0.80)
    handle(result)

    content = log_file.read_text(encoding="utf-8")
    data = json.loads(content.strip())
    assert data["level"] == "warning"
    assert data["metric"] == "recharge_success_rate"
    assert data["value"] == 0.75


def test_handle_ok_does_not_write_to_file(tmp_path, monkeypatch):
    import engine.alert_engine as ae
    log_file = tmp_path / "alerts.log"
    monkeypatch.setattr(ae, "ALERT_LOG_PATH", log_file)

    result = make_rule_result("ok", "none")
    handle(result)

    assert not log_file.exists()
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
.venv/Scripts/pytest tests/test_alert_engine.py -v
```

Expected: `ImportError`。

- [ ] **Step 3: 实现 `engine/alert_engine.py`**

```python
# engine/alert_engine.py
# 告警引擎：Phase 1 实现结构化日志输出 + 写 logs/alerts.log
# Phase 2 接入 Telegram 时只扩展此模块，接口不变
import json
import logging
import time
from pathlib import Path
from engine.models import RuleResult

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

ALERT_LOG_PATH = Path(__file__).parent.parent / "logs" / "alerts.log"


def handle(result: RuleResult) -> None:
    if result.level == "ok":
        return

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

    if result.level == "warning":
        logger.warning(result.message)
    elif result.level == "critical":
        logger.critical(result.message)

    ALERT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ALERT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
.venv/Scripts/pytest tests/test_alert_engine.py -v
```

Expected: 5 passed。

- [ ] **Step 5: Commit**

```bash
git add engine/alert_engine.py tests/test_alert_engine.py
git commit -m "feat: add alert engine (log + file output)"
```

---

## Task 8: 执行层（executor/action_executor.py）

**Files:**
- Create: `executor/action_executor.py`
- Create: `db/init_monitor.sql`
- Create: `tests/test_action_executor.py`

- [ ] **Step 1: 创建建表 SQL `db/init_monitor.sql`**

```sql
CREATE DATABASE IF NOT EXISTS tg_monitor DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE tg_monitor;

CREATE TABLE IF NOT EXISTS monitor_action_queue (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  domain       VARCHAR(32)   NOT NULL COMMENT '监控域: payment/game/risk/activity/account/operation',
  action_type  VARCHAR(64)   NOT NULL COMMENT '动作类型: switch_channel/freeze_order/alert...',
  target_id    VARCHAR(64)   NULL     COMMENT '操作对象ID（渠道ID、订单号等）',
  payload      JSON          NOT NULL COMMENT '执行参数',
  status       TINYINT       NOT NULL DEFAULT 0 COMMENT '0-待审批 1-已批准 2-已执行 3-已拒绝',
  priority     TINYINT       NOT NULL DEFAULT 2 COMMENT '1-紧急 2-普通 3-低优先级',
  triggered_by VARCHAR(128)  NOT NULL COMMENT '触发指标描述',
  metric_value DECIMAL(18,4) NULL     COMMENT '触发时的指标值',
  threshold    DECIMAL(18,4) NULL     COMMENT '触发时的阈值',
  operator     VARCHAR(64)   NULL     COMMENT '审批人',
  approved_at  BIGINT        NULL,
  executed_at  BIGINT        NULL,
  created_at   BIGINT        NOT NULL,
  updated_at   BIGINT        NOT NULL
);
```

- [ ] **Step 2: 写失败测试**

新建 `tests/test_action_executor.py`：

```python
import time
import pytest
from unittest.mock import MagicMock, patch
from executor.action_executor import enqueue_action


def make_mock_conn():
    cursor = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn, cursor


def test_enqueue_action_inserts_row():
    conn, cursor = make_mock_conn()

    with patch("executor.action_executor.get_monitor_conn", return_value=conn):
        enqueue_action(
            domain="payment",
            action_type="switch_channel",
            target_id="ch_001",
            payload={"from": "ch_001", "to": "ch_002"},
            priority=1,
            triggered_by="recharge_success_rate",
            metric_value=0.55,
            threshold=0.60,
        )

    cursor.execute.assert_called_once()
    sql, args = cursor.execute.call_args
    assert "INSERT INTO monitor_action_queue" in sql[0]
    assert args[0] == "payment"
    assert args[1] == "switch_channel"
    assert args[2] == "ch_001"
    conn.commit.assert_called_once()
    conn.close.assert_called_once()


def test_enqueue_action_target_id_none():
    conn, cursor = make_mock_conn()

    with patch("executor.action_executor.get_monitor_conn", return_value=conn):
        enqueue_action(
            domain="payment",
            action_type="alert",
            target_id=None,
            payload={"note": "test"},
            priority=2,
            triggered_by="withdraw_queue_count",
            metric_value=60.0,
            threshold=50.0,
        )

    _, args = cursor.execute.call_args
    assert args[2] is None
```

- [ ] **Step 3: 运行测试，确认失败**

```bash
.venv/Scripts/pytest tests/test_action_executor.py -v
```

Expected: `ImportError`。

- [ ] **Step 4: 实现 `executor/action_executor.py`**

```python
# executor/action_executor.py
# 执行层：将需人工审批或高危动作写入 tg_monitor.monitor_action_queue
import json
import time
from config.db import get_monitor_conn


def enqueue_action(
    domain: str,
    action_type: str,
    target_id: str | None,
    payload: dict,
    priority: int,
    triggered_by: str,
    metric_value: float | None,
    threshold: float | None,
) -> None:
    now = int(time.time())
    conn = get_monitor_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO monitor_action_queue
                  (domain, action_type, target_id, payload, status, priority,
                   triggered_by, metric_value, threshold, created_at, updated_at)
                VALUES (%s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s)
                """,
                (
                    domain,
                    action_type,
                    target_id,
                    json.dumps(payload, ensure_ascii=False),
                    priority,
                    triggered_by,
                    metric_value,
                    threshold,
                    now,
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 5: 运行测试，确认通过**

```bash
.venv/Scripts/pytest tests/test_action_executor.py -v
```

Expected: 2 passed。

- [ ] **Step 6: Commit**

```bash
git add executor/action_executor.py db/init_monitor.sql tests/test_action_executor.py
git commit -m "feat: add action executor and monitor_action_queue DDL"
```

---

## Task 9: 支付域监控采集器（monitor/payment_monitor.py）

**Files:**
- Create: `monitor/payment_monitor.py`
- Create: `tests/test_payment_monitor.py`

4 个采集函数均接收 `conn`（tian-gong 连接）和 `thresholds` dict，返回 `list[MetricResult]`。调度器负责传入连接，函数本身不开连接（便于测试）。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_payment_monitor.py`：

```python
import time
import pytest
from unittest.mock import MagicMock, patch
from monitor.payment_monitor import (
    check_recharge,
    check_channel_balance,
    check_withdraw_queue,
    check_withdraw_fail_rate,
)

THRESHOLDS = {
    "recharge": {
        "pending_timeout_minutes": 30,
        "pending_count_warning": 20,
    },
    "withdraw": {
        "queue_timeout_hours": 4,
        "fail_rate_warning": 0.20,
    },
    "channel_account": {
        "balance_warning_amount": 10000,
    },
}


def make_cursor(rows):
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    return cursor


def make_conn(rows):
    conn = MagicMock()
    conn.cursor.return_value = make_cursor(rows)
    return conn


def test_check_recharge_returns_metrics_per_channel():
    rows = [
        {"channel_id": 1, "channel_name": "渠道A", "total": 100, "success": 85, "timeout": 5, "pending_overdue": 3},
        {"channel_id": 2, "channel_name": "渠道B", "total": 50,  "success": 25, "timeout": 2, "pending_overdue": 0},
    ]
    conn = make_conn(rows)
    results = check_recharge(conn, THRESHOLDS)

    assert len(results) == 6  # 每渠道 3 条: success_rate / timeout_rate / pending_count
    metrics = {(r.channel_id, r.metric) for r in results}
    assert (1, "recharge_success_rate") in metrics
    assert (1, "recharge_timeout_rate") in metrics
    assert (1, "recharge_pending_count") in metrics
    assert (2, "recharge_success_rate") in metrics


def test_check_recharge_success_rate_calculation():
    rows = [{"channel_id": 1, "channel_name": "A", "total": 100, "success": 80, "timeout": 0, "pending_overdue": 0}]
    conn = make_conn(rows)
    results = check_recharge(conn, THRESHOLDS)
    sr = next(r for r in results if r.metric == "recharge_success_rate")
    assert sr.value == pytest.approx(0.80)


def test_check_recharge_empty_returns_no_results():
    conn = make_conn([])
    results = check_recharge(conn, THRESHOLDS)
    assert results == []


def test_check_channel_balance_returns_one_per_account():
    rows = [
        {"account_id": 10, "account_name": "账户A", "balance": 5000.0},
        {"account_id": 11, "account_name": "账户B", "balance": 20000.0},
    ]
    conn = make_conn(rows)
    results = check_channel_balance(conn, THRESHOLDS)
    assert len(results) == 2
    assert all(r.metric == "channel_balance" for r in results)
    values = {r.channel_id: r.value for r in results}
    assert values[10] == 5000.0


def test_check_withdraw_queue_returns_single_metric():
    rows = [{"overdue_count": 60}]
    conn = make_conn(rows)
    results = check_withdraw_queue(conn, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "withdraw_queue_count"
    assert results[0].value == 60


def test_check_withdraw_fail_rate_with_data():
    rows = [{"total_processed": 100, "failed": 25}]
    conn = make_conn(rows)
    results = check_withdraw_fail_rate(conn, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "withdraw_fail_rate"
    assert results[0].value == pytest.approx(0.25)


def test_check_withdraw_fail_rate_no_data():
    rows = [{"total_processed": 0, "failed": 0}]
    conn = make_conn(rows)
    results = check_withdraw_fail_rate(conn, THRESHOLDS)
    assert results == []
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
.venv/Scripts/pytest tests/test_payment_monitor.py -v
```

Expected: `ImportError`。

- [ ] **Step 3: 实现 `monitor/payment_monitor.py`**

```python
# monitor/payment_monitor.py
# 支付域监控采集器（P0）：查询 tian-gong 库，返回 MetricResult 列表
# 不直接开连接，由调度器传入，便于测试和连接管理
import time
from engine.models import MetricResult


def check_recharge(conn, thresholds: dict) -> list[MetricResult]:
    """分渠道采集：充值成功率 / 超时率 / 积压数。"""
    cfg = thresholds["recharge"]
    cutoff = int(time.time()) - cfg["pending_timeout_minutes"] * 60

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                pc.id                                        AS channel_id,
                pc.name                                      AS channel_name,
                COUNT(*)                                     AS total,
                SUM(pr.status = 1)                          AS success,
                SUM(pr.status = 3)                          AS timeout,
                SUM(pr.status = 0 AND pr.created_at < %s)  AS pending_overdue
            FROM pay_recharge pr
            JOIN pay_channel pc ON pc.id = pr.channel_id
            WHERE pr.created_at >= %s
            GROUP BY pc.id, pc.name
            """,
            (cutoff, int(time.time()) - 3600),
        )
        rows = cur.fetchall()

    results = []
    for row in rows:
        total = row["total"] or 0
        if total == 0:
            continue
        ch_id = row["channel_id"]
        ch_name = row["channel_name"]
        extra = {"channel_name": ch_name, "order_count": total}

        results.append(MetricResult(
            domain="payment", metric="recharge_success_rate",
            value=(row["success"] or 0) / total,
            channel_id=ch_id, extra=extra,
        ))
        results.append(MetricResult(
            domain="payment", metric="recharge_timeout_rate",
            value=(row["timeout"] or 0) / total,
            channel_id=ch_id, extra=extra,
        ))
        results.append(MetricResult(
            domain="payment", metric="recharge_pending_count",
            value=float(row["pending_overdue"] or 0),
            channel_id=ch_id, extra=extra,
        ))
    return results


def check_channel_balance(conn, thresholds: dict) -> list[MetricResult]:
    """采集所有渠道账号余额。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id AS account_id, name AS account_name, balance FROM pay_channel_account"
        )
        rows = cur.fetchall()

    return [
        MetricResult(
            domain="payment", metric="channel_balance",
            value=float(row["balance"]),
            channel_id=row["account_id"],
            extra={"account_name": row["account_name"]},
        )
        for row in rows
    ]


def check_withdraw_queue(conn, thresholds: dict) -> list[MetricResult]:
    """采集提现审核积压数（状态=0 且超 queue_timeout_hours）。"""
    cfg = thresholds["withdraw"]
    cutoff = int(time.time()) - int(cfg["queue_timeout_hours"] * 3600)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS overdue_count FROM pay_withdraw WHERE status = 0 AND created_at < %s",
            (cutoff,),
        )
        row = cur.fetchall()[0]

    return [MetricResult(
        domain="payment", metric="withdraw_queue_count",
        value=float(row["overdue_count"]),
    )]


def check_withdraw_fail_rate(conn, thresholds: dict) -> list[MetricResult]:
    """采集最近 1 小时提现失败率（status=4 / 已处理总数）。"""
    since = int(time.time()) - 3600

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                COUNT(*) AS total_processed,
                SUM(status = 4) AS failed
            FROM pay_withdraw
            WHERE status IN (3, 4) AND updated_at >= %s
            """,
            (since,),
        )
        row = cur.fetchall()[0]

    total = row["total_processed"] or 0
    if total == 0:
        return []

    return [MetricResult(
        domain="payment", metric="withdraw_fail_rate",
        value=(row["failed"] or 0) / total,
        extra={"sample_count": total},
    )]
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
.venv/Scripts/pytest tests/test_payment_monitor.py -v
```

Expected: 8 passed。

- [ ] **Step 5: Commit**

```bash
git add monitor/payment_monitor.py tests/test_payment_monitor.py
git commit -m "feat: add payment monitor (recharge/balance/withdraw)"
```

---

## Task 10: 调度器（scheduler.py）

**Files:**
- Create: `scheduler.py`

- [ ] **Step 1: 创建 `scheduler.py`**

```python
# scheduler.py
# 常驻调度器：APScheduler interval 驱动支付域监控全流程
# 运行方式: python scheduler.py
import asyncio
import logging
import time

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


async def job_check_recharge():
    logger.info("[payment] 执行充值监控")
    await asyncio.get_event_loop().run_in_executor(None, _run_monitor, check_recharge)


async def job_check_channel_balance():
    logger.info("[payment] 执行渠道账号余额监控")
    await asyncio.get_event_loop().run_in_executor(None, _run_monitor, check_channel_balance)


async def job_check_withdraw_queue():
    logger.info("[payment] 执行提现积压监控")
    await asyncio.get_event_loop().run_in_executor(None, _run_monitor, check_withdraw_queue)


async def job_check_withdraw_fail_rate():
    logger.info("[payment] 执行提现失败率监控")
    await asyncio.get_event_loop().run_in_executor(None, _run_monitor, check_withdraw_fail_rate)


def _on_job_error(event):
    logger.error(f"调度任务异常: {event.job_id} — {event.exception}")


def main():
    payment_cfg = load_thresholds("payment")

    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)

    scheduler.add_job(
        lambda: asyncio.ensure_future(job_check_recharge()),
        "interval",
        minutes=payment_cfg["recharge"]["check_interval_minutes"],
        id="payment_recharge",
    )
    scheduler.add_job(
        lambda: asyncio.ensure_future(job_check_channel_balance()),
        "interval",
        minutes=payment_cfg["channel_account"]["check_interval_minutes"],
        id="payment_channel_balance",
    )
    scheduler.add_job(
        lambda: asyncio.ensure_future(job_check_withdraw_queue()),
        "interval",
        minutes=payment_cfg["withdraw"]["check_interval_minutes"],
        id="payment_withdraw_queue",
    )
    scheduler.add_job(
        lambda: asyncio.ensure_future(job_check_withdraw_fail_rate()),
        "interval",
        minutes=payment_cfg["withdraw"]["fail_rate_interval_minutes"],
        id="payment_withdraw_fail_rate",
    )

    scheduler.start()
    logger.info("监控调度器已启动，Ctrl+C 退出")
    try:
        asyncio.get_event_loop().run_forever()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logger.info("调度器已停止")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 验证调度器可以启动（无 DB 时至少不报 ImportError）**

```bash
.venv/Scripts/python -c "import scheduler; print('import OK')"
```

Expected: 打印 `import OK`，无 `ImportError`。

- [ ] **Step 3: Commit**

```bash
git add scheduler.py
git commit -m "feat: add APScheduler-based scheduler for payment domain"
```

---

## Task 11: 全量测试 + 收尾

**Files:** 无新文件，验证整体。

- [ ] **Step 1: 运行全部测试**

```bash
.venv/Scripts/pytest tests/ -v
```

Expected: 全部 pass（test_db、test_threshold_config、test_rule_engine、test_alert_engine、test_action_executor、test_payment_monitor）。

- [ ] **Step 2: 检查目录结构与设计文档一致**

```bash
find . -not -path './.venv/*' -not -path './.git/*' -not -path './docs/*' -type f | sort
```

确认所有文件存在：`config/db.py`、`config/thresholds_*.json`（6 个）、`engine/models.py`、`engine/threshold_config.py`、`engine/rule_engine.py`、`engine/alert_engine.py`、`monitor/payment_monitor.py`、`executor/action_executor.py`、`db/init_monitor.sql`、`scheduler.py`。

- [ ] **Step 3: 最终提交**

```bash
git add -A
git commit -m "chore: Phase 1 complete - payment monitor + rule/alert/executor engines"
```
