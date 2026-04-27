# 天工平台系统运营监控 Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增游戏供应商域监控、风控域监控，以及实时 HTTP 监控看板（三域汇总卡片 + 告警流）。

**Architecture:** 延续 Phase 1 数据流（monitor → rule_engine → alert_engine/action_executor），在 scheduler 的每个 job 完成后额外调用 `dashboard.update()` 将结果写入 HTML 文件，`http.server` 后台线程持续 serve 该文件。`_run_monitor` 重构为接受 domain 参数，统一处理多域阈值加载。

**Tech Stack:** Python 3.10+, PyMySQL, APScheduler, python-dotenv, pytest, http.server（内置）

---

## 文件清单

| 操作 | 路径 | 职责 |
|---|---|---|
| Create | `monitor/game_monitor.py` | 游戏供应商域 2 个采集函数 |
| Create | `monitor/risk_monitor.py` | 风控域 4 个采集函数 |
| Create | `dashboard/__init__.py` | 包初始化 |
| Create | `dashboard/monitor_dashboard.py` | MonitorDashboard 类：HTTP server + HTML 生成 |
| Create | `output/.gitkeep` | 保留 output 目录 |
| Modify | `engine/rule_engine.py` | 追加 7 个规则函数 + 扩展 _RULES dict |
| Modify | `scheduler.py` | 重构 _run_monitor，新增 6 个 job，集成 dashboard |
| Create | `tests/test_game_monitor.py` | game_monitor 单元测试 |
| Create | `tests/test_risk_monitor.py` | risk_monitor 单元测试 |
| Create | `tests/test_rule_engine_phase2.py` | Phase 2 规则单元测试 |
| Create | `tests/test_monitor_dashboard.py` | dashboard 单元测试 |

---

## Task 1: game_monitor.py

**Files:**
- Create: `monitor/game_monitor.py`
- Create: `tests/test_game_monitor.py`

**背景：**
- `gam_balance_transfer`：游戏余额转账记录，`status=2` 表示失败，`retry_count` 是重试次数，按 `manufacturer_id` 分组（供应商维度）
- `sys_manuf_reconciliation_daily`：厂商日对账表，`reconcile_status=2` 表示有差异，按 `manufacturer_id` + 连续天数统计
- 阈值从 `thresholds_game.json` 读取：`balance_transfer.fail_rate_warning=0.05`，`balance_transfer.retry_order_count_warning=5`，`reconciliation.diff_consecutive_days_critical=2`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_game_monitor.py`：

```python
import pytest
from unittest.mock import MagicMock
from monitor.game_monitor import check_balance_transfer, check_reconciliation

THRESHOLDS = {
    "balance_transfer": {
        "retry_count_warning": 3,
        "retry_order_count_warning": 5,
    },
    "reconciliation": {
        "diff_consecutive_days_critical": 2,
        "diff_amount_warning": 100,
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


def test_check_balance_transfer_returns_metrics_per_manufacturer():
    rows = [
        {"manufacturer_id": 1, "manufacturer_name": "供应商A", "total": 100, "failed": 3, "retry_anomaly_count": 2},
        {"manufacturer_id": 2, "manufacturer_name": "供应商B", "total": 50, "failed": 5, "retry_anomaly_count": 6},
    ]
    conn = make_conn(rows)
    results = check_balance_transfer(conn, THRESHOLDS)

    assert len(results) == 4  # 每供应商 2 条: fail_rate + retry_count
    metrics = {(r.channel_id, r.metric) for r in results}
    assert (1, "game_transfer_fail_rate") in metrics
    assert (1, "game_transfer_retry_count") in metrics
    assert (2, "game_transfer_fail_rate") in metrics
    assert (2, "game_transfer_retry_count") in metrics


def test_check_balance_transfer_fail_rate_calculation():
    rows = [{"manufacturer_id": 1, "manufacturer_name": "A", "total": 100, "failed": 5, "retry_anomaly_count": 0}]
    conn = make_conn(rows)
    results = check_balance_transfer(conn, THRESHOLDS)
    rate = next(r for r in results if r.metric == "game_transfer_fail_rate")
    assert rate.value == pytest.approx(0.05)


def test_check_balance_transfer_empty():
    conn = make_conn([])
    results = check_balance_transfer(conn, THRESHOLDS)
    assert results == []


def test_check_reconciliation_returns_diff_days_per_manufacturer():
    rows = [
        {"manufacturer_id": 1, "manufacturer_name": "供应商A", "consecutive_diff_days": 3},
        {"manufacturer_id": 2, "manufacturer_name": "供应商B", "consecutive_diff_days": 0},
    ]
    conn = make_conn(rows)
    results = check_reconciliation(conn, THRESHOLDS)

    assert len(results) == 2
    assert all(r.metric == "game_reconciliation_diff_days" for r in results)
    vals = {r.channel_id: r.value for r in results}
    assert vals[1] == 3.0
    assert vals[2] == 0.0
```

- [ ] **Step 2: 运行测试确认失败**

```
"D:\Projects\PycharmProjects\System Monitor\.venv\Scripts\pytest" tests/test_game_monitor.py -v
```

Expected: `ImportError`（`monitor/game_monitor.py` 尚未创建）

- [ ] **Step 3: 实现 `monitor/game_monitor.py`**

```python
# monitor/game_monitor.py
# 游戏供应商域监控采集器（P1）：按供应商维度采集转账失败率和对账差异
import time
from engine.models import MetricResult


def check_balance_transfer(conn, thresholds: dict) -> list[MetricResult]:
    """分供应商采集：余额转账失败率 / 重试异常订单数。"""
    cfg = thresholds["balance_transfer"]
    retry_threshold = cfg["retry_count_warning"]

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                manufacturer_id,
                manufacturer_name,
                COUNT(*)                                    AS total,
                SUM(status = 2)                             AS failed,
                SUM(retry_count >= %s)                      AS retry_anomaly_count
            FROM gam_balance_transfer
            WHERE created_at >= %s
            GROUP BY manufacturer_id, manufacturer_name
            """,
            (retry_threshold, int(time.time()) - 3600),
        )
        rows = cur.fetchall()

    results = []
    for row in rows:
        total = row["total"] or 0
        if total == 0:
            continue
        mid = row["manufacturer_id"]
        mname = row["manufacturer_name"] or f"供应商{mid}"
        extra = {"manufacturer_name": mname, "order_count": total}

        results.append(MetricResult(
            domain="game", metric="game_transfer_fail_rate",
            value=(row["failed"] or 0) / total,
            channel_id=mid, extra=extra,
        ))
        results.append(MetricResult(
            domain="game", metric="game_transfer_retry_count",
            value=float(row["retry_anomaly_count"] or 0),
            channel_id=mid, extra=extra,
        ))
    return results


def check_reconciliation(conn, thresholds: dict) -> list[MetricResult]:
    """采集各供应商连续对账差异天数。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                manufacturer_id,
                manufacturer_name,
                COUNT(*) AS consecutive_diff_days
            FROM sys_manuf_reconciliation_daily
            WHERE reconcile_status = 2
              AND stat_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
            GROUP BY manufacturer_id, manufacturer_name
            """
        )
        rows = cur.fetchall()

    return [
        MetricResult(
            domain="game", metric="game_reconciliation_diff_days",
            value=float(row["consecutive_diff_days"] or 0),
            channel_id=row["manufacturer_id"],
            extra={"manufacturer_name": row["manufacturer_name"] or f"供应商{row['manufacturer_id']}"},
        )
        for row in rows
    ]
```

- [ ] **Step 4: 运行测试确认通过**

```
"D:\Projects\PycharmProjects\System Monitor\.venv\Scripts\pytest" tests/test_game_monitor.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add monitor/game_monitor.py tests/test_game_monitor.py
git commit -m "feat: add game monitor (balance transfer + reconciliation)"
```

---

## Task 2: risk_monitor.py

**Files:**
- Create: `monitor/risk_monitor.py`
- Create: `tests/test_risk_monitor.py`

**背景：**
- `rsk_alert`：风险预警表，`alert_level>=3` 为高危，`handle_status=0` 为未处理
- `rsk_event`：风控事件表，`event_level=3` 为高危，`handle_status=0` 为未处理
- `rsk_blacklist`：黑名单表，`expire_type=2` 为临时，`expire_time` 为过期时间戳
- 阈值从 `thresholds_risk.json` 读取：`alert.high_risk_pending_warning=10`，`alert.high_risk_timeout_hours=2`，`event.high_risk_pending_warning=5`，`blacklist.expiry_reminder_hours=24`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_risk_monitor.py`：

```python
import time
import pytest
from unittest.mock import MagicMock
from monitor.risk_monitor import (
    check_alert_backlog,
    check_alert_timeout,
    check_event_backlog,
    check_blacklist_expiry,
)

THRESHOLDS = {
    "alert": {
        "high_risk_pending_warning": 10,
        "high_risk_timeout_hours": 2,
    },
    "event": {
        "high_risk_pending_warning": 5,
    },
    "blacklist": {
        "expiry_reminder_hours": 24,
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


def test_check_alert_backlog_returns_single_metric():
    rows = [{"backlog_count": 15}]
    conn = make_conn(rows)
    results = check_alert_backlog(conn, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "risk_alert_backlog_count"
    assert results[0].value == 15.0


def test_check_alert_timeout_returns_single_metric():
    rows = [{"timeout_count": 3}]
    conn = make_conn(rows)
    results = check_alert_timeout(conn, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "risk_alert_timeout_count"
    assert results[0].value == 3.0


def test_check_event_backlog_returns_single_metric():
    rows = [{"backlog_count": 7}]
    conn = make_conn(rows)
    results = check_event_backlog(conn, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "risk_event_backlog_count"
    assert results[0].value == 7.0


def test_check_blacklist_expiry_returns_single_metric():
    rows = [{"expiry_count": 2}]
    conn = make_conn(rows)
    results = check_blacklist_expiry(conn, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "risk_blacklist_expiry_count"
    assert results[0].value == 2.0
```

- [ ] **Step 2: 运行测试确认失败**

```
"D:\Projects\PycharmProjects\System Monitor\.venv\Scripts\pytest" tests/test_risk_monitor.py -v
```

Expected: `ImportError`

- [ ] **Step 3: 实现 `monitor/risk_monitor.py`**

```python
# monitor/risk_monitor.py
# 风控域监控采集器（P1）：高危预警积压 / 超时 / 风控事件积压 / 黑名单到期
import time
from engine.models import MetricResult


def check_alert_backlog(conn, thresholds: dict) -> list[MetricResult]:
    """采集高危风控预警积压数（alert_level>=3 且未处理）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS backlog_count FROM rsk_alert WHERE alert_level >= 3 AND handle_status = 0"
        )
        row = cur.fetchall()[0]

    return [MetricResult(
        domain="risk", metric="risk_alert_backlog_count",
        value=float(row["backlog_count"] or 0),
    )]


def check_alert_timeout(conn, thresholds: dict) -> list[MetricResult]:
    """采集高危预警超时未处理数（未处理且超过 high_risk_timeout_hours）。"""
    cfg = thresholds["alert"]
    cutoff = int(time.time()) - int(cfg["high_risk_timeout_hours"] * 3600)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS timeout_count
            FROM rsk_alert
            WHERE alert_level >= 3
              AND handle_status = 0
              AND created_at < %s
            """,
            (cutoff,),
        )
        row = cur.fetchall()[0]

    return [MetricResult(
        domain="risk", metric="risk_alert_timeout_count",
        value=float(row["timeout_count"] or 0),
    )]


def check_event_backlog(conn, thresholds: dict) -> list[MetricResult]:
    """采集高危风控事件积压数（event_level=3 且未处理）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS backlog_count FROM rsk_event WHERE event_level = 3 AND handle_status = 0"
        )
        row = cur.fetchall()[0]

    return [MetricResult(
        domain="risk", metric="risk_event_backlog_count",
        value=float(row["backlog_count"] or 0),
    )]


def check_blacklist_expiry(conn, thresholds: dict) -> list[MetricResult]:
    """采集临时黑名单即将到期数（expire_type=2 且在 expiry_reminder_hours 内到期）。"""
    cfg = thresholds["blacklist"]
    deadline = int(time.time()) + int(cfg["expiry_reminder_hours"] * 3600)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS expiry_count
            FROM rsk_blacklist
            WHERE expire_type = 2
              AND expire_time <= %s
              AND expire_time > %s
            """,
            (deadline, int(time.time())),
        )
        row = cur.fetchall()[0]

    return [MetricResult(
        domain="risk", metric="risk_blacklist_expiry_count",
        value=float(row["expiry_count"] or 0),
    )]
```

- [ ] **Step 4: 运行测试确认通过**

```
"D:\Projects\PycharmProjects\System Monitor\.venv\Scripts\pytest" tests/test_risk_monitor.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add monitor/risk_monitor.py tests/test_risk_monitor.py
git commit -m "feat: add risk monitor (alert backlog/timeout/event/blacklist expiry)"
```

---

## Task 3: rule_engine.py 新增游戏域 + 风控域规则

**Files:**
- Modify: `engine/rule_engine.py`
- Create: `tests/test_rule_engine_phase2.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_rule_engine_phase2.py`：

```python
import pytest
from engine.models import MetricResult
from engine.rule_engine import evaluate

GAME_THRESHOLDS = {
    "balance_transfer": {
        "fail_rate_warning": 0.05,
        "retry_order_count_warning": 5,
    },
    "reconciliation": {
        "diff_consecutive_days_critical": 2,
    },
}

RISK_THRESHOLDS = {
    "alert": {
        "high_risk_pending_warning": 10,
    },
    "event": {
        "high_risk_pending_warning": 5,
    },
}


def make_metric(metric, value, domain="game", channel_id=None):
    return MetricResult(domain=domain, metric=metric, value=value, channel_id=channel_id)


# --- game rules ---

def test_game_transfer_fail_rate_ok():
    results = evaluate([make_metric("game_transfer_fail_rate", 0.03)], GAME_THRESHOLDS)
    assert results[0].level == "ok"


def test_game_transfer_fail_rate_warning():
    results = evaluate([make_metric("game_transfer_fail_rate", 0.08)], GAME_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"
    assert results[0].threshold == 0.05


def test_game_transfer_retry_count_ok():
    results = evaluate([make_metric("game_transfer_retry_count", 3.0)], GAME_THRESHOLDS)
    assert results[0].level == "ok"


def test_game_transfer_retry_count_warning():
    results = evaluate([make_metric("game_transfer_retry_count", 6.0)], GAME_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"


def test_game_reconciliation_ok():
    results = evaluate([make_metric("game_reconciliation_diff_days", 1.0)], GAME_THRESHOLDS)
    assert results[0].level == "ok"


def test_game_reconciliation_critical():
    results = evaluate([make_metric("game_reconciliation_diff_days", 3.0)], GAME_THRESHOLDS)
    assert results[0].level == "critical"
    assert results[0].action == "enqueue"
    assert results[0].threshold == 2


# --- risk rules ---

def test_risk_alert_backlog_ok():
    results = evaluate([make_metric("risk_alert_backlog_count", 5.0, domain="risk")], RISK_THRESHOLDS)
    assert results[0].level == "ok"


def test_risk_alert_backlog_warning():
    results = evaluate([make_metric("risk_alert_backlog_count", 15.0, domain="risk")], RISK_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"


def test_risk_alert_timeout_zero_ok():
    results = evaluate([make_metric("risk_alert_timeout_count", 0.0, domain="risk")], RISK_THRESHOLDS)
    assert results[0].level == "ok"


def test_risk_alert_timeout_nonzero_warning():
    results = evaluate([make_metric("risk_alert_timeout_count", 1.0, domain="risk")], RISK_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"


def test_risk_event_backlog_warning():
    results = evaluate([make_metric("risk_event_backlog_count", 8.0, domain="risk")], RISK_THRESHOLDS)
    assert results[0].level == "warning"


def test_risk_blacklist_expiry_zero_ok():
    results = evaluate([make_metric("risk_blacklist_expiry_count", 0.0, domain="risk")], RISK_THRESHOLDS)
    assert results[0].level == "ok"


def test_risk_blacklist_expiry_nonzero_warning():
    results = evaluate([make_metric("risk_blacklist_expiry_count", 2.0, domain="risk")], RISK_THRESHOLDS)
    assert results[0].level == "warning"
    assert results[0].action == "alert"
```

- [ ] **Step 2: 运行测试确认失败**

```
"D:\Projects\PycharmProjects\System Monitor\.venv\Scripts\pytest" tests/test_rule_engine_phase2.py -v
```

Expected: 13 tests，全部 FAILED（规则函数尚未添加）

- [ ] **Step 3: 在 `engine/rule_engine.py` 末尾追加规则函数并扩展 `_RULES`**

在文件末尾（`_RULES = {...}` 之前）追加以下函数，然后扩展 `_RULES`：

```python
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
```

将 `_RULES` dict 替换为：

```python
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
}
```

- [ ] **Step 4: 运行全量测试（含 Phase 1 回归）**

```
"D:\Projects\PycharmProjects\System Monitor\.venv\Scripts\pytest" tests/test_rule_engine.py tests/test_rule_engine_phase2.py -v
```

Expected: 12 + 13 = 25 passed，0 failed

- [ ] **Step 5: Commit**

```bash
git add engine/rule_engine.py tests/test_rule_engine_phase2.py
git commit -m "feat: add game and risk domain rules to rule engine (7 new rules)"
```

---

## Task 4: monitor_dashboard.py

**Files:**
- Create: `dashboard/__init__.py`
- Create: `dashboard/monitor_dashboard.py`
- Create: `output/.gitkeep`
- Create: `tests/test_monitor_dashboard.py`

- [ ] **Step 1: 创建目录和 `__init__.py`**

```bash
mkdir -p "D:/Projects/PycharmProjects/System Monitor/dashboard"
mkdir -p "D:/Projects/PycharmProjects/System Monitor/output"
touch "D:/Projects/PycharmProjects/System Monitor/dashboard/__init__.py"
touch "D:/Projects/PycharmProjects/System Monitor/output/.gitkeep"
```

- [ ] **Step 2: 写失败测试**

新建 `tests/test_monitor_dashboard.py`：

```python
import json
import time
from pathlib import Path
from engine.models import MetricResult, RuleResult
from dashboard.monitor_dashboard import MonitorDashboard


def make_rule_result(level, action, domain="payment", metric="recharge_success_rate", value=0.5, threshold=0.8, msg="test"):
    m = MetricResult(domain=domain, metric=metric, value=value)
    return RuleResult(level=level, action=action, metric=m, threshold=threshold, message=msg)


def test_update_writes_html_file(tmp_path, monkeypatch):
    dashboard = MonitorDashboard(port=0)
    monkeypatch.setattr(dashboard, "_html_path", tmp_path / "dashboard.html")

    results = [
        make_rule_result("critical", "enqueue", msg="充值成功率严重低于阈值"),
        make_rule_result("warning", "alert", msg="渠道余额不足"),
        make_rule_result("ok", "none"),
    ]
    dashboard.update("payment", results)

    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "天工平台" in html
    assert "payment" in html
    assert "充值成功率严重低于阈值" in html
    assert "渠道余额不足" in html


def test_update_tracks_domain_summary(tmp_path, monkeypatch):
    dashboard = MonitorDashboard(port=0)
    monkeypatch.setattr(dashboard, "_html_path", tmp_path / "dashboard.html")

    results = [
        make_rule_result("critical", "enqueue"),
        make_rule_result("warning", "alert"),
        make_rule_result("ok", "none"),
    ]
    dashboard.update("payment", results)

    summary = dashboard.get_domain_summary("payment")
    assert summary["critical"] == 1
    assert summary["warning"] == 1
    assert summary["ok"] == 1


def test_recent_alerts_only_non_ok(tmp_path, monkeypatch):
    dashboard = MonitorDashboard(port=0)
    monkeypatch.setattr(dashboard, "_html_path", tmp_path / "dashboard.html")

    results = [
        make_rule_result("critical", "enqueue", msg="alert1"),
        make_rule_result("warning", "alert", msg="alert2"),
        make_rule_result("ok", "none", msg="ok_msg"),
    ]
    dashboard.update("payment", results)

    alerts = list(dashboard.recent_alerts)
    assert len(alerts) == 2
    assert all(r.level != "ok" for r in alerts)


def test_update_multiple_domains(tmp_path, monkeypatch):
    dashboard = MonitorDashboard(port=0)
    monkeypatch.setattr(dashboard, "_html_path", tmp_path / "dashboard.html")

    dashboard.update("payment", [make_rule_result("warning", "alert")])
    dashboard.update("game", [make_rule_result("ok", "none", domain="game")])

    assert "payment" in dashboard.domain_results
    assert "game" in dashboard.domain_results
```

- [ ] **Step 3: 运行测试确认失败**

```
"D:\Projects\PycharmProjects\System Monitor\.venv\Scripts\pytest" tests/test_monitor_dashboard.py -v
```

Expected: `ImportError`

- [ ] **Step 4: 实现 `dashboard/monitor_dashboard.py`**

```python
# dashboard/monitor_dashboard.py
# 实时监控看板：HTTP server serve 静态 HTML，scheduler 每轮更新后刷新文件
import http.server
import threading
import time
from collections import deque
from pathlib import Path
from engine.models import RuleResult

_OUTPUT_DIR = Path(__file__).parent.parent / "output"
_DOMAIN_LABELS = {
    "payment": "支付域",
    "game": "游戏供应商域",
    "risk": "风控域",
}
_LEVEL_COLOR = {
    "critical": "#e94560",
    "warning": "#f5a623",
    "ok": "#27ae60",
}


class MonitorDashboard:
    def __init__(self, port: int = 8080):
        self._port = port
        self._html_path = _OUTPUT_DIR / "monitor_dashboard.html"
        self.domain_results: dict[str, list[RuleResult]] = {}
        self.recent_alerts: deque[RuleResult] = deque(maxlen=50)
        self._last_update: str = "--"
        self._server: http.server.HTTPServer | None = None

    def start(self) -> None:
        if self._port == 0:
            return
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self._render()

        html_dir = str(_OUTPUT_DIR)

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=html_dir, **kwargs)

            def log_message(self, fmt, *args):
                pass  # 静默 HTTP 日志

        self._server = http.server.HTTPServer(("0.0.0.0", self._port), Handler)
        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()

    def update(self, domain: str, results: list[RuleResult]) -> None:
        self.domain_results[domain] = results
        for r in results:
            if r.level != "ok":
                self.recent_alerts.appendleft(r)
        self._last_update = time.strftime("%H:%M:%S")
        self._render()

    def get_domain_summary(self, domain: str) -> dict:
        results = self.domain_results.get(domain, [])
        return {
            "critical": sum(1 for r in results if r.level == "critical"),
            "warning": sum(1 for r in results if r.level == "warning"),
            "ok": sum(1 for r in results if r.level == "ok"),
        }

    def _render(self) -> None:
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        cards_html = ""
        for domain_key in ["payment", "game", "risk"]:
            label = _DOMAIN_LABELS.get(domain_key, domain_key)
            summary = self.get_domain_summary(domain_key)
            if summary["critical"] > 0:
                border_color = _LEVEL_COLOR["critical"]
                status_text = f"⚠ {summary['critical']} CRITICAL"
                status_color = _LEVEL_COLOR["critical"]
            elif summary["warning"] > 0:
                border_color = _LEVEL_COLOR["warning"]
                status_text = f"△ {summary['warning']} WARNING"
                status_color = _LEVEL_COLOR["warning"]
            else:
                border_color = _LEVEL_COLOR["ok"]
                status_text = "✓ 正常"
                status_color = _LEVEL_COLOR["ok"]

            cards_html += f"""
            <div style="background:#0f3460;padding:16px;border-radius:8px;border-left:4px solid {border_color}">
              <div style="color:#aaa;font-size:12px;margin-bottom:6px">{label}</div>
              <div style="color:{status_color};font-size:18px;font-weight:bold">{status_text}</div>
              <div style="color:#666;font-size:12px;margin-top:4px">
                {summary['critical']} critical &nbsp;|&nbsp; {summary['warning']} warning &nbsp;|&nbsp; {summary['ok']} ok
              </div>
            </div>"""

        alerts_html = ""
        for r in list(self.recent_alerts)[:20]:
            color = _LEVEL_COLOR.get(r.level, "#aaa")
            label = _DOMAIN_LABELS.get(r.metric.domain, r.metric.domain)
            alerts_html += f"""
            <div style="padding:8px 12px;border-bottom:1px solid #1a1a2e;font-size:13px">
              <span style="color:{color};font-weight:bold">[{r.level.upper()}]</span>
              <span style="color:#666;font-size:11px;margin:0 8px">[{label}]</span>
              <span style="color:#ccc">{r.message}</span>
            </div>"""

        if not alerts_html:
            alerts_html = '<div style="padding:16px;color:#555;text-align:center">暂无告警</div>'

        html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="30">
  <title>天工平台 系统监控</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #1a1a2e; color: #ccc; font-family: 'Courier New', monospace; }}
    .header {{ background: #16213e; padding: 12px 24px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #0f3460; }}
    .header h1 {{ color: #e94560; font-size: 16px; }}
    .header .meta {{ color: #555; font-size: 12px; }}
    .cards {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; padding: 20px 24px; }}
    .alerts {{ padding: 0 24px 24px; }}
    .alerts h2 {{ color: #aaa; font-size: 13px; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 1px; }}
    .alert-box {{ background: #0f3460; border-radius: 8px; overflow: hidden; }}
  </style>
</head>
<body>
  <div class="header">
    <h1>⬤ 天工平台 系统监控</h1>
    <div class="meta">最后更新: {self._last_update} &nbsp;|&nbsp; 每30秒自动刷新</div>
  </div>
  <div class="cards">{cards_html}</div>
  <div class="alerts">
    <h2>最新告警</h2>
    <div class="alert-box">{alerts_html}</div>
  </div>
</body>
</html>"""

        self._html_path.write_text(html, encoding="utf-8")
```

- [ ] **Step 5: 运行测试确认通过**

```
"D:\Projects\PycharmProjects\System Monitor\.venv\Scripts\pytest" tests/test_monitor_dashboard.py -v
```

Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add dashboard/__init__.py dashboard/monitor_dashboard.py output/.gitkeep tests/test_monitor_dashboard.py
git commit -m "feat: add monitor dashboard (HTTP server + HTML with domain cards + alert feed)"
```

---

## Task 5: scheduler.py 重构 + 新增 6 个 job + 集成 dashboard

**Files:**
- Modify: `scheduler.py`

**重构说明：** 将 `_run_monitor` 改为接受 `domain` 参数，从对应域的 JSON 加载阈值，调用结束后通知 dashboard。

- [ ] **Step 1: 将 `scheduler.py` 完整替换为以下内容**

```python
# scheduler.py
# 常驻调度器：APScheduler interval/cron 驱动全域监控，集成实时看板
# 运行方式: python scheduler.py
import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.events import EVENT_JOB_ERROR

from config.db import get_source_conn
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
                        "value": result.metric.value,
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


def _on_job_error(event):
    logger.error(f"调度任务异常: {event.job_id} — {event.exception}")


def main():
    payment_cfg = load_thresholds("payment")
    game_cfg = load_thresholds("game")
    risk_cfg = load_thresholds("risk")

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

    dashboard.start()
    logger.info("监控看板已启动: http://localhost:8080/monitor_dashboard.html")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    scheduler.start()
    logger.info("监控调度器已启动，Ctrl+C 退出")
    try:
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logger.info("调度器已停止")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 验证 import 正常**

```
"D:\Projects\PycharmProjects\System Monitor\.venv\Scripts\python" -c "import scheduler; print('import OK')"
```

Expected: `import OK`，无 ImportError

- [ ] **Step 3: 运行全量测试确认无回归**

```
"D:\Projects\PycharmProjects\System Monitor\.venv\Scripts\pytest" tests/ -v
```

Expected: 全部 passed（含 Phase 1 的 30 个 + Phase 2 新增 25 个 = 约 55 个）

- [ ] **Step 4: Commit**

```bash
git add scheduler.py
git commit -m "feat: refactor scheduler with domain param, add game/risk jobs and dashboard integration"
```

---

## Task 6: 全量测试 + 收尾

**Files:** 无新文件，验证整体。

- [ ] **Step 1: 运行全量测试**

```
"D:\Projects\PycharmProjects\System Monitor\.venv\Scripts\pytest" tests/ -v
```

Expected: 全部 passed，0 failed

- [ ] **Step 2: 检查新增文件全部存在**

```bash
find "D:/Projects/PycharmProjects/System Monitor" \
  -not -path "*/.venv/*" -not -path "*/.git/*" \
  -not -path "*/docs/*" -not -path "*/__pycache__/*" \
  -not -path "*/.pytest_cache/*" -not -path "*/.superpowers/*" \
  -type f | sort
```

确认以下文件存在：
- `dashboard/__init__.py`
- `dashboard/monitor_dashboard.py`
- `monitor/game_monitor.py`
- `monitor/risk_monitor.py`
- `output/.gitkeep`
- `tests/test_game_monitor.py`
- `tests/test_risk_monitor.py`
- `tests/test_rule_engine_phase2.py`
- `tests/test_monitor_dashboard.py`

- [ ] **Step 3: 最终提交**

```bash
git add -A
git status
# 确认没有遗漏的未跟踪文件
git commit -m "chore: Phase 2 complete - game/risk monitors + dashboard" --allow-empty
```

- [ ] **Step 4: Push**

```bash
git push
```
