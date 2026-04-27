# Phase 3 监控域实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增活动域、用户账户域、系统操作域三个监控域，完整接入采集 → 规则 → 调度 → 看板流水线。

**Architecture:** 每个域一个独立 monitor 文件，遵循 Phase 1/2 相同模式：采集函数接收 `conn + thresholds`，返回 `list[MetricResult]`；规则追加到 `engine/rule_engine.py` 的 `_RULES` dict；调度器在 `scheduler.py` 里新增 async job 函数并注册；看板 `_DOMAIN_LABELS` + `_render()` 扩展到 6 域。

**Tech Stack:** Python 3.11+, PyMySQL, APScheduler 3.x, dataclasses

---

## 文件清单

| 操作 | 文件 |
|---|---|
| 新建 | `monitor/activity_monitor.py` |
| 新建 | `monitor/account_monitor.py` |
| 新建 | `monitor/operation_monitor.py` |
| 新建 | `tests/test_activity_monitor.py` |
| 新建 | `tests/test_account_monitor.py` |
| 新建 | `tests/test_operation_monitor.py` |
| 新建 | `tests/test_phase3_rules.py` |
| 修改 | `config/thresholds_activity.json` |
| 修改 | `config/thresholds_operation.json` |
| 修改 | `engine/rule_engine.py` |
| 修改 | `scheduler.py` |
| 修改 | `dashboard/monitor_dashboard.py` |

---

## Task 1: 更新阈值配置文件

**Files:**
- Modify: `config/thresholds_activity.json`
- Modify: `config/thresholds_operation.json`

- [ ] **Step 1: 更新 thresholds_activity.json**

完整替换为：

```json
{
  "_comment": "比率单位: 0-1，数量单位: 条，时间单位: 分钟",
  "redemption": {
    "fail_rate_warning": 0.05,
    "fail_count_warning": 10,
    "retry_count_warning": 3,
    "retry_order_count_warning": 10,
    "check_interval_minutes": 15
  },
  "first_deposit": {
    "fail_alert_threshold": 1,
    "volume_zero_check": true,
    "check_interval_minutes": 5
  }
}
```

- [ ] **Step 2: 更新 thresholds_operation.json**

完整替换为（修正 `balance_adjustment.check_interval_minutes` 从 0 改为 10）：

```json
{
  "_comment": "时间单位: 小时，金额单位: 元，数量单位: 次",
  "vip_adjust": {
    "batch_count_per_hour_warning": 20,
    "check_interval_minutes": 60
  },
  "balance_adjustment": {
    "large_amount_threshold": 10000,
    "check_interval_minutes": 10
  },
  "config_change": {
    "change_count_per_hour_warning": 10,
    "check_interval_minutes": 60
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add config/thresholds_activity.json config/thresholds_operation.json
git commit -m "config: add missing keys for phase3 activity/operation thresholds"
```

---

## Task 2: 实现 activity_monitor.py

**Files:**
- Create: `monitor/activity_monitor.py`
- Create: `tests/test_activity_monitor.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_activity_monitor.py`：

```python
import pytest
from unittest.mock import MagicMock
from monitor.activity_monitor import check_redemption, check_first_deposit


def _make_conn(rows_by_sql):
    """返回一个按调用顺序返回不同结果的 mock conn。"""
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    cur.fetchall.side_effect = rows_by_sql
    return conn


THRESHOLDS = {
    "redemption": {
        "fail_rate_warning": 0.05,
        "fail_count_warning": 10,
        "check_interval_minutes": 15,
    },
    "first_deposit": {
        "fail_alert_threshold": 1,
        "volume_zero_check": True,
        "check_interval_minutes": 5,
    },
}


def test_check_redemption_returns_two_metrics():
    conn = _make_conn([
        [{"total": 100, "fail": 8}],
    ])
    results = check_redemption(conn, THRESHOLDS)
    assert len(results) == 2
    metrics = {r.metric for r in results}
    assert "redemption_fail_rate" in metrics
    assert "redemption_fail_count" in metrics


def test_check_redemption_calculates_rate():
    conn = _make_conn([
        [{"total": 100, "fail": 8}],
    ])
    results = check_redemption(conn, THRESHOLDS)
    rate = next(r for r in results if r.metric == "redemption_fail_rate")
    assert abs(rate.value - 0.08) < 1e-9


def test_check_redemption_zero_total():
    conn = _make_conn([
        [{"total": 0, "fail": 0}],
    ])
    results = check_redemption(conn, THRESHOLDS)
    rate = next(r for r in results if r.metric == "redemption_fail_rate")
    assert rate.value == 0.0


def test_check_first_deposit_returns_two_metrics():
    conn = _make_conn([
        [{"fail_count": 2, "total_count": 50}],
    ])
    results = check_first_deposit(conn, THRESHOLDS)
    assert len(results) == 2
    metrics = {r.metric for r in results}
    assert "first_deposit_fail_count" in metrics
    assert "first_deposit_volume" in metrics


def test_check_first_deposit_has_is_business_hours():
    conn = _make_conn([
        [{"fail_count": 0, "total_count": 10}],
    ])
    results = check_first_deposit(conn, THRESHOLDS)
    volume = next(r for r in results if r.metric == "first_deposit_volume")
    assert "is_business_hours" in volume.extra
    assert isinstance(volume.extra["is_business_hours"], bool)
```

- [ ] **Step 2: 运行确认测试失败**

```bash
pytest tests/test_activity_monitor.py -v
```

Expected: `ImportError` 或 `ModuleNotFoundError`（文件尚未创建）

- [ ] **Step 3: 实现 monitor/activity_monitor.py**

```python
import time
from datetime import datetime
from engine.models import MetricResult


def _is_business_hours() -> bool:
    """业务高峰时段判断：10:00-23:00。"""
    hour = datetime.now().hour
    return 10 <= hour <= 23


def check_redemption(conn, thresholds: dict) -> list[MetricResult]:
    """采集活动兑换失败率和失败数量。"""
    cfg = thresholds["redemption"]
    window = int(cfg["check_interval_minutes"] * 60)
    cutoff = int(time.time()) - window

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status != 1 THEN 1 ELSE 0 END) AS fail
            FROM act_activity_redemption
            WHERE created_at >= %s
            """,
            (cutoff,),
        )
        row = cur.fetchall()[0]

    total = int(row["total"] or 0)
    fail = int(row["fail"] or 0)
    rate = fail / total if total > 0 else 0.0

    return [
        MetricResult(
            domain="activity", metric="redemption_fail_rate",
            value=rate,
            extra={"total_count": total, "fail_count": fail},
        ),
        MetricResult(
            domain="activity", metric="redemption_fail_count",
            value=float(fail),
            extra={"total_count": total},
        ),
    ]


def check_first_deposit(conn, thresholds: dict) -> list[MetricResult]:
    """采集首充异常条目数和首充量。"""
    cfg = thresholds["first_deposit"]
    window = int(cfg["check_interval_minutes"] * 60)
    cutoff = int(time.time()) - window

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS total_count,
                   SUM(CASE WHEN status != 1 THEN 1 ELSE 0 END) AS fail_count
            FROM act_first_deposit_record
            WHERE created_at >= %s
            """,
            (cutoff,),
        )
        row = cur.fetchall()[0]

    total = int(row["total_count"] or 0)
    fail = int(row["fail_count"] or 0)

    return [
        MetricResult(
            domain="activity", metric="first_deposit_fail_count",
            value=float(fail),
        ),
        MetricResult(
            domain="activity", metric="first_deposit_volume",
            value=float(total),
            extra={"is_business_hours": _is_business_hours()},
        ),
    ]
```

- [ ] **Step 4: 运行确认测试通过**

```bash
pytest tests/test_activity_monitor.py -v
```

Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add monitor/activity_monitor.py tests/test_activity_monitor.py
git commit -m "feat: add activity_monitor (redemption + first_deposit)"
```

---

## Task 3: 实现 account_monitor.py

**Files:**
- Create: `monitor/account_monitor.py`
- Create: `tests/test_account_monitor.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_account_monitor.py`：

```python
from unittest.mock import MagicMock
from monitor.account_monitor import check_frozen_balance


def _make_conn(rows):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    cur.fetchall.side_effect = rows
    return conn


THRESHOLDS = {
    "frozen_balance": {
        "growth_rate_warning": 0.50,
        "check_interval_minutes": 60,
    }
}


def test_check_frozen_balance_returns_one_metric():
    conn = _make_conn([
        [{"current_frozen": 12000.0}],
        [{"prev_frozen": 10000.0}],
    ])
    results = check_frozen_balance(conn, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "frozen_balance_growth_rate"


def test_check_frozen_balance_calculates_growth_rate():
    conn = _make_conn([
        [{"current_frozen": 15000.0}],
        [{"prev_frozen": 10000.0}],
    ])
    results = check_frozen_balance(conn, THRESHOLDS)
    assert abs(results[0].value - 0.5) < 1e-9


def test_check_frozen_balance_zero_previous():
    conn = _make_conn([
        [{"current_frozen": 5000.0}],
        [{"prev_frozen": 0.0}],
    ])
    results = check_frozen_balance(conn, THRESHOLDS)
    assert results[0].value == 0.0


def test_check_frozen_balance_extra_has_amounts():
    conn = _make_conn([
        [{"current_frozen": 12000.0}],
        [{"prev_frozen": 10000.0}],
    ])
    results = check_frozen_balance(conn, THRESHOLDS)
    assert results[0].extra["current_amount"] == 12000.0
    assert results[0].extra["previous_amount"] == 10000.0
```

- [ ] **Step 2: 运行确认测试失败**

```bash
pytest tests/test_account_monitor.py -v
```

Expected: `ImportError`

- [ ] **Step 3: 实现 monitor/account_monitor.py**

```python
import time
from engine.models import MetricResult


def check_frozen_balance(conn, thresholds: dict) -> list[MetricResult]:
    """采集冻结余额增长率（当前 vs N 分钟前）。"""
    cfg = thresholds["frozen_balance"]
    window = int(cfg["check_interval_minutes"] * 60)
    cutoff = int(time.time()) - window

    with conn.cursor() as cur:
        cur.execute("SELECT SUM(frozen_balance) AS current_frozen FROM usr_account")
        current_row = cur.fetchall()[0]

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT SUM(frozen_balance) AS prev_frozen
            FROM usr_account
            WHERE updated_at <= %s
            """,
            (cutoff,),
        )
        prev_row = cur.fetchall()[0]

    current = float(current_row["current_frozen"] or 0)
    previous = float(prev_row["prev_frozen"] or 0)
    rate = (current - previous) / previous if previous > 0 else 0.0

    return [
        MetricResult(
            domain="account", metric="frozen_balance_growth_rate",
            value=rate,
            extra={"current_amount": current, "previous_amount": previous},
        )
    ]
```

- [ ] **Step 4: 运行确认测试通过**

```bash
pytest tests/test_account_monitor.py -v
```

Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add monitor/account_monitor.py tests/test_account_monitor.py
git commit -m "feat: add account_monitor (frozen_balance)"
```

---

## Task 4: 实现 operation_monitor.py

**Files:**
- Create: `monitor/operation_monitor.py`
- Create: `tests/test_operation_monitor.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_operation_monitor.py`：

```python
from unittest.mock import MagicMock
from monitor.operation_monitor import check_vip_adjust, check_balance_adjustment, check_config_change


def _make_conn(rows):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    cur.fetchall.side_effect = rows
    return conn


THRESHOLDS = {
    "vip_adjust": {
        "batch_count_per_hour_warning": 20,
        "check_interval_minutes": 60,
    },
    "balance_adjustment": {
        "large_amount_threshold": 10000,
        "check_interval_minutes": 10,
    },
    "config_change": {
        "change_count_per_hour_warning": 10,
        "check_interval_minutes": 60,
    },
}


def test_check_vip_adjust_returns_one_metric():
    conn = _make_conn([[{"adjust_count": 5}]])
    results = check_vip_adjust(conn, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "vip_adjust_count"
    assert results[0].value == 5.0


def test_check_balance_adjustment_returns_one_metric():
    conn = _make_conn([[{"large_count": 3, "max_amount": 50000.0}]])
    results = check_balance_adjustment(conn, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "balance_adjustment_large_count"
    assert results[0].value == 3.0
    assert results[0].extra["max_amount"] == 50000.0


def test_check_balance_adjustment_zero_rows():
    conn = _make_conn([[{"large_count": 0, "max_amount": None}]])
    results = check_balance_adjustment(conn, THRESHOLDS)
    assert results[0].value == 0.0
    assert results[0].extra["max_amount"] == 0.0


def test_check_config_change_returns_one_metric():
    conn = _make_conn([[{"change_count": 7}]])
    results = check_config_change(conn, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "config_change_count"
    assert results[0].value == 7.0
```

- [ ] **Step 2: 运行确认测试失败**

```bash
pytest tests/test_operation_monitor.py -v
```

Expected: `ImportError`

- [ ] **Step 3: 实现 monitor/operation_monitor.py**

```python
import time
from engine.models import MetricResult


def check_vip_adjust(conn, thresholds: dict) -> list[MetricResult]:
    """采集过去 1 小时 VIP 调整操作次数。"""
    cutoff = int(time.time()) - 3600

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS adjust_count FROM adm_vip_adjust_log WHERE created_at >= %s",
            (cutoff,),
        )
        row = cur.fetchall()[0]

    return [MetricResult(
        domain="operation", metric="vip_adjust_count",
        value=float(row["adjust_count"] or 0),
    )]


def check_balance_adjustment(conn, thresholds: dict) -> list[MetricResult]:
    """采集过去 N 分钟内大额余额调整笔数。"""
    cfg = thresholds["balance_adjustment"]
    window = int(cfg["check_interval_minutes"] * 60)
    cutoff = int(time.time()) - window
    threshold_amount = cfg["large_amount_threshold"]

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS large_count, MAX(ABS(amount)) AS max_amount
            FROM pay_balance_adjustment
            WHERE created_at >= %s AND ABS(amount) >= %s
            """,
            (cutoff, threshold_amount),
        )
        row = cur.fetchall()[0]

    return [MetricResult(
        domain="operation", metric="balance_adjustment_large_count",
        value=float(row["large_count"] or 0),
        extra={"max_amount": float(row["max_amount"] or 0)},
    )]


def check_config_change(conn, thresholds: dict) -> list[MetricResult]:
    """采集过去 1 小时配置变更操作次数。"""
    cutoff = int(time.time()) - 3600

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS change_count
            FROM sys_operation_log
            WHERE operation_type = 'config_change' AND created_at >= %s
            """,
            (cutoff,),
        )
        row = cur.fetchall()[0]

    return [MetricResult(
        domain="operation", metric="config_change_count",
        value=float(row["change_count"] or 0),
    )]
```

- [ ] **Step 4: 运行确认测试通过**

```bash
pytest tests/test_operation_monitor.py -v
```

Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add monitor/operation_monitor.py tests/test_operation_monitor.py
git commit -m "feat: add operation_monitor (vip_adjust + balance_adjustment + config_change)"
```

---

## Task 5: 扩展 rule_engine.py — 新增 7 条规则

**Files:**
- Modify: `engine/rule_engine.py`
- Create: `tests/test_phase3_rules.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_phase3_rules.py`：

```python
from engine.models import MetricResult
from engine.rule_engine import evaluate

THRESHOLDS_ACTIVITY = {
    "redemption": {"fail_rate_warning": 0.05, "fail_count_warning": 10},
    "first_deposit": {"fail_alert_threshold": 1, "volume_zero_check": True},
}
THRESHOLDS_ACCOUNT = {
    "frozen_balance": {"growth_rate_warning": 0.50},
}
THRESHOLDS_OPERATION = {
    "vip_adjust": {"batch_count_per_hour_warning": 20},
    "balance_adjustment": {"large_amount_threshold": 10000},
    "config_change": {"change_count_per_hour_warning": 10},
}


def _metric(metric, value, domain, extra=None):
    return MetricResult(domain=domain, metric=metric, value=value, extra=extra or {})


# --- activity ---

def test_redemption_fail_rate_warning():
    m = _metric("redemption_fail_rate", 0.08, "activity")
    result = evaluate([m], THRESHOLDS_ACTIVITY)[0]
    assert result.level == "warning"
    assert result.action == "alert"


def test_redemption_fail_rate_ok():
    m = _metric("redemption_fail_rate", 0.03, "activity")
    result = evaluate([m], THRESHOLDS_ACTIVITY)[0]
    assert result.level == "ok"


def test_redemption_fail_count_warning():
    m = _metric("redemption_fail_count", 15.0, "activity")
    result = evaluate([m], THRESHOLDS_ACTIVITY)[0]
    assert result.level == "warning"
    assert result.action == "alert"


def test_first_deposit_fail_count_warning():
    m = _metric("first_deposit_fail_count", 1.0, "activity")
    result = evaluate([m], THRESHOLDS_ACTIVITY)[0]
    assert result.level == "warning"
    assert result.action == "alert"


def test_first_deposit_fail_count_ok():
    m = _metric("first_deposit_fail_count", 0.0, "activity")
    result = evaluate([m], THRESHOLDS_ACTIVITY)[0]
    assert result.level == "ok"


def test_first_deposit_volume_zero_during_business_hours():
    m = _metric("first_deposit_volume", 0.0, "activity", extra={"is_business_hours": True})
    result = evaluate([m], THRESHOLDS_ACTIVITY)[0]
    assert result.level == "warning"


def test_first_deposit_volume_zero_outside_business_hours():
    m = _metric("first_deposit_volume", 0.0, "activity", extra={"is_business_hours": False})
    result = evaluate([m], THRESHOLDS_ACTIVITY)[0]
    assert result.level == "ok"


def test_first_deposit_volume_nonzero():
    m = _metric("first_deposit_volume", 50.0, "activity", extra={"is_business_hours": True})
    result = evaluate([m], THRESHOLDS_ACTIVITY)[0]
    assert result.level == "ok"


# --- account ---

def test_frozen_balance_growth_warning():
    m = _metric("frozen_balance_growth_rate", 0.60, "account",
                extra={"current_amount": 16000.0, "previous_amount": 10000.0})
    result = evaluate([m], THRESHOLDS_ACCOUNT)[0]
    assert result.level == "warning"
    assert result.action == "alert"


def test_frozen_balance_growth_ok():
    m = _metric("frozen_balance_growth_rate", 0.20, "account",
                extra={"current_amount": 12000.0, "previous_amount": 10000.0})
    result = evaluate([m], THRESHOLDS_ACCOUNT)[0]
    assert result.level == "ok"


# --- operation ---

def test_vip_adjust_count_warning():
    m = _metric("vip_adjust_count", 25.0, "operation")
    result = evaluate([m], THRESHOLDS_OPERATION)[0]
    assert result.level == "warning"
    assert result.action == "enqueue"


def test_vip_adjust_count_ok():
    m = _metric("vip_adjust_count", 5.0, "operation")
    result = evaluate([m], THRESHOLDS_OPERATION)[0]
    assert result.level == "ok"


def test_balance_adjustment_large_count_warning():
    m = _metric("balance_adjustment_large_count", 1.0, "operation",
                extra={"max_amount": 50000.0})
    result = evaluate([m], THRESHOLDS_OPERATION)[0]
    assert result.level == "warning"
    assert result.action == "enqueue"


def test_balance_adjustment_large_count_ok():
    m = _metric("balance_adjustment_large_count", 0.0, "operation",
                extra={"max_amount": 0.0})
    result = evaluate([m], THRESHOLDS_OPERATION)[0]
    assert result.level == "ok"


def test_config_change_count_warning():
    m = _metric("config_change_count", 12.0, "operation")
    result = evaluate([m], THRESHOLDS_OPERATION)[0]
    assert result.level == "warning"
    assert result.action == "alert"


def test_config_change_count_ok():
    m = _metric("config_change_count", 5.0, "operation")
    result = evaluate([m], THRESHOLDS_OPERATION)[0]
    assert result.level == "ok"
```

- [ ] **Step 2: 运行确认测试失败**

```bash
pytest tests/test_phase3_rules.py -v
```

Expected: 多数 FAIL（规则尚未注册）

- [ ] **Step 3: 在 rule_engine.py 追加 7 条规则函数**

在 `engine/rule_engine.py` 中，在 `_RULES` dict 之前追加以下函数：

```python
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


def _activity_redemption_fail_count(m: MetricResult, t: dict) -> RuleResult:
    threshold = t["redemption"]["fail_count_warning"]
    if m.value > threshold:
        return RuleResult(
            level="warning", action="alert", metric=m, threshold=threshold,
            message=f"活动兑换失败数超限: {int(m.value)} 笔 > {threshold} 笔",
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


def _activity_first_deposit_volume(m: MetricResult, t: dict) -> RuleResult:
    if not t["first_deposit"].get("volume_zero_check", False):
        return RuleResult(level="ok", action="none", metric=m, threshold=0.0, message="")
    if m.value == 0 and m.extra.get("is_business_hours", False):
        return RuleResult(
            level="warning", action="alert", metric=m, threshold=0.0,
            message="业务高峰时段首充量为零，疑似系统异常",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=0.0, message="")


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
```

- [ ] **Step 4: 在 _RULES dict 注册新规则**

在 `engine/rule_engine.py` 的 `_RULES` dict 末尾追加：

```python
    # activity domain
    "redemption_fail_rate": _activity_redemption_fail_rate,
    "redemption_fail_count": _activity_redemption_fail_count,
    "first_deposit_fail_count": _activity_first_deposit_fail_count,
    "first_deposit_volume": _activity_first_deposit_volume,
    # account domain
    "frozen_balance_growth_rate": _account_frozen_balance_growth,
    # operation domain
    "vip_adjust_count": _operation_vip_adjust_count,
    "balance_adjustment_large_count": _operation_balance_adjustment_large,
    "config_change_count": _operation_config_change_count,
```

- [ ] **Step 5: 运行确认测试通过**

```bash
pytest tests/test_phase3_rules.py -v
```

Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add engine/rule_engine.py tests/test_phase3_rules.py
git commit -m "feat: add phase3 rules (activity/account/operation domains)"
```

---

## Task 6: 扩展 scheduler.py — 新增 6 个 job

**Files:**
- Modify: `scheduler.py`

- [ ] **Step 1: 在 scheduler.py 顶部 import 区追加新 monitor 导入**

在现有 `from monitor.risk_monitor import (...)` 之后追加：

```python
from monitor.activity_monitor import check_redemption, check_first_deposit
from monitor.account_monitor import check_frozen_balance
from monitor.operation_monitor import (
    check_vip_adjust,
    check_balance_adjustment,
    check_config_change,
)
```

- [ ] **Step 2: 在 scheduler.py 风控 job 之后追加 6 个 async job 函数**

在 `job_check_blacklist_expiry` 函数之后、`_on_job_error` 之前追加：

```python
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
```

- [ ] **Step 3: 在 main() 函数内加载配置并注册 6 个 job**

在 `main()` 函数中，`risk_cfg = load_thresholds("risk")` 之后追加：

```python
    activity_cfg = load_thresholds("activity")
    account_cfg = load_thresholds("account")
    operation_cfg = load_thresholds("operation")
```

在 risk job 注册区块之后追加：

```python
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
```

- [ ] **Step 4: 验证 scheduler.py 语法正确**

```bash
python -c "import scheduler; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add scheduler.py
git commit -m "feat: register phase3 jobs in scheduler (activity/account/operation)"
```

---

## Task 7: 扩展 dashboard — 新增三个域卡片

**Files:**
- Modify: `dashboard/monitor_dashboard.py`

- [ ] **Step 1: 更新 _DOMAIN_LABELS**

将 `dashboard/monitor_dashboard.py` 中的 `_DOMAIN_LABELS` 替换为：

```python
_DOMAIN_LABELS = {
    "payment": "支付域",
    "game": "游戏供应商域",
    "risk": "风控域",
    "activity": "活动域",
    "account": "账户域",
    "operation": "系统操作域",
}
```

- [ ] **Step 2: 更新 _render() 中的域遍历列表**

将 `_render()` 方法中的：

```python
        for domain_key in ["payment", "game", "risk"]:
```

替换为：

```python
        for domain_key in ["payment", "game", "risk", "activity", "account", "operation"]:
```

- [ ] **Step 3: 验证看板渲染正常**

```bash
python -c "
from dashboard.monitor_dashboard import MonitorDashboard
d = MonitorDashboard(port=0)
d.start()
d.update('activity', [])
d.update('account', [])
d.update('operation', [])
print('render OK, cards:', len(d.domain_results))
"
```

Expected: `render OK, cards: 3`

- [ ] **Step 4: Commit**

```bash
git add dashboard/monitor_dashboard.py
git commit -m "feat: extend dashboard to 6 domains (activity/account/operation)"
```

---

## Task 8: 全量测试 + 完工确认

- [ ] **Step 1: 运行所有测试**

```bash
pytest tests/ -v
```

Expected: 全部 PASS，无 FAIL

- [ ] **Step 2: 检查 scheduler.py 导入无报错**

```bash
python -c "import scheduler; print('scheduler import OK')"
```

Expected: `scheduler import OK`

- [ ] **Step 3: 更新内存文件**

更新 `C:\Users\49868\.claude\projects\D--Projects-PycharmProjects-System-Monitor\memory\project_progress.md`，将 Phase 3 标记为 ✅ 完成。

- [ ] **Step 4: 最终 commit**

```bash
git add -A
git commit -m "chore: Phase 3 complete - activity/account/operation monitors"
```
