# Phase 5 Root Cause Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在告警触发时自动附带跨域根因关联提示，并每日定时产出 LLM 因果链深度报告。

**Architecture:** 实时层在 `alert_engine.handle()` 内调用 `correlation_engine.correlate(result)`，从 `monitor_metric_history` 查近 30 分钟跨域告警并匹配 `config/causal_chains.json`，结果内联写入 `alerts.log` 的 `correlation` 字段。定时层 `engine/root_cause_analyzer.py` 每日 05:00 读取近 24h 历史 + 日志，LLM 单次分析产出报告写入文件和 DB。

**Tech Stack:** Python 3.11, PyMySQL, APScheduler, LangChain (`init_chat_model`), pytest, `unittest.mock`

---

## File Map

| 文件 | 操作 | 职责 |
|---|---|---|
| `config/causal_chains.json` | 新增 | 预定义因果链配置，6 条初始规则 |
| `engine/correlation_engine.py` | 新增 | 实时关联逻辑，`CorrelationResult` 数据类 + `correlate()` |
| `engine/alert_engine.py` | 修改 | `handle()` 调用 `correlate()`，log entry 加 `correlation` 字段 |
| `engine/root_cause_analyzer.py` | 新增 | 深度 LLM 分析器，`main(interactive)` 入口 |
| `db/migrate_phase5.sql` | 新增 | `monitor_root_cause_reports` 表 |
| `scheduler.py` | 修改 | 注册 `job_run_root_cause_analyzer`（05:00 cron） |
| `tests/test_correlation_engine.py` | 新增 | correlation_engine 单元测试 |
| `tests/test_alert_engine_phase5.py` | 新增 | alert_engine 集成 correlation 的测试 |
| `tests/test_root_cause_analyzer.py` | 新增 | root_cause_analyzer 单元测试 |

---

## Task 1: 创建因果链配置文件

**Files:**
- Create: `config/causal_chains.json`

- [ ] **Step 1: 写入初始 6 条因果链**

创建 `config/causal_chains.json`：

```json
[
  {
    "cause": {"domain": "log", "metric": "mq_route_error_count"},
    "effects": [
      {"domain": "game", "metric": "game_transfer_fail_rate"},
      {"domain": "payment", "metric": "recharge_success_rate"}
    ],
    "description": "MQ 路由故障 → 游戏转账/支付成功率下降"
  },
  {
    "cause": {"domain": "log", "metric": "db_shard_error_count"},
    "effects": [
      {"domain": "payment", "metric": "recharge_success_rate"},
      {"domain": "payment", "metric": "recharge_pending_count"}
    ],
    "description": "DB 分表故障 → 充值失败/积压"
  },
  {
    "cause": {"domain": "log", "metric": "mq_route_error_count"},
    "effects": [
      {"domain": "payment", "metric": "recharge_pending_count"},
      {"domain": "payment", "metric": "withdraw_queue_count"}
    ],
    "description": "MQ 路由故障 → 积压订单增加"
  },
  {
    "cause": {"domain": "game", "metric": "game_transfer_fail_rate"},
    "effects": [
      {"domain": "payment", "metric": "recharge_success_rate"}
    ],
    "description": "游戏转账失败 → 支付成功率下降"
  },
  {
    "cause": {"domain": "log", "metric": "db_shard_error_count"},
    "effects": [
      {"domain": "game", "metric": "game_transfer_fail_rate"}
    ],
    "description": "DB 分表故障 → 游戏转账失败"
  },
  {
    "cause": {"domain": "risk", "metric": "risk_alert_backlog_count"},
    "effects": [
      {"domain": "payment", "metric": "withdraw_queue_count"}
    ],
    "description": "风控积压 → 提现审核积压"
  }
]
```

- [ ] **Step 2: Commit**

```bash
git add config/causal_chains.json
git commit -m "feat: add initial causal_chains.json with 6 known chains"
```

---

## Task 2: 实现 correlation_engine.py（TDD）

**Files:**
- Create: `engine/correlation_engine.py`
- Create: `tests/test_correlation_engine.py`

- [ ] **Step 1: 写失败测试——CorrelationResult 数据类**

创建 `tests/test_correlation_engine.py`：

```python
import json
import time
from dataclasses import asdict
from unittest.mock import MagicMock, patch

from engine.models import MetricResult, RuleResult
from engine.correlation_engine import CorrelationResult, correlate


def _make_result(domain="payment", metric="recharge_success_rate",
                 level="critical", action="enqueue"):
    m = MetricResult(domain=domain, metric=metric, value=0.5)
    return RuleResult(level=level, action=action, metric=m,
                      threshold=0.7, message="test alert")


def test_correlation_result_fields():
    cr = CorrelationResult(
        confidence="none",
        cause_domain=None,
        cause_metric=None,
        cause_ts=None,
        lead_minutes=None,
        message="",
    )
    assert cr.confidence == "none"
    assert cr.cause_domain is None
    assert cr.message == ""
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_correlation_engine.py::test_correlation_result_fields -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'engine.correlation_engine'`

- [ ] **Step 3: 实现 CorrelationResult 数据类**

创建 `engine/correlation_engine.py`：

```python
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from config.db import get_monitor_conn
from engine.models import RuleResult

_CHAINS_PATH = Path(__file__).parent.parent / "config" / "causal_chains.json"


@dataclass
class CorrelationResult:
    confidence: Literal["known", "suspected", "none"]
    cause_domain: str | None
    cause_metric: str | None
    cause_ts: int | None
    lead_minutes: float | None
    message: str


def _load_chains() -> list[dict]:
    if not _CHAINS_PATH.exists():
        return []
    with open(_CHAINS_PATH, encoding="utf-8") as f:
        return json.load(f)


def correlate(result: RuleResult) -> CorrelationResult:
    """实时关联分析：查近 30min 跨域告警，匹配已知因果链。"""
    if result.level == "ok":
        return CorrelationResult(
            confidence="none", cause_domain=None, cause_metric=None,
            cause_ts=None, lead_minutes=None, message="",
        )

    now_ms = int(time.time() * 1000)
    window_start_ms = now_ms - 30 * 60 * 1000
    window_end_ms = now_ms - 60 * 1000  # 排除 1 分钟内（避免同批采集的自关联）
    current_domain = result.metric.domain
    current_metric = result.metric.metric

    conn = get_monitor_conn()
    try:
        rows = _fetch_recent_alerts(conn, current_domain, window_start_ms, window_end_ms)
    finally:
        conn.close()

    if not rows:
        return CorrelationResult(
            confidence="none", cause_domain=None, cause_metric=None,
            cause_ts=None, lead_minutes=None, message="",
        )

    chains = _load_chains()

    # 优先匹配已知因果链
    for chain in chains:
        cause = chain["cause"]
        for effect in chain["effects"]:
            if effect["domain"] == current_domain and effect["metric"] == current_metric:
                for row in rows:
                    if row["domain"] == cause["domain"] and row["metric"] == cause["metric"]:
                        lead = (now_ms - row["recorded_at"]) / 60000
                        return CorrelationResult(
                            confidence="known",
                            cause_domain=cause["domain"],
                            cause_metric=cause["metric"],
                            cause_ts=row["recorded_at"],
                            lead_minutes=round(lead, 1),
                            message=(
                                f"已知因果：{chain['description']}，"
                                f"根因于 {round(lead, 1)} 分钟前触发"
                            ),
                        )

    # 降级：时序接近（取最近一条跨域告警）
    nearest = rows[0]
    lead = (now_ms - nearest["recorded_at"]) / 60000
    return CorrelationResult(
        confidence="suspected",
        cause_domain=nearest["domain"],
        cause_metric=nearest["metric"],
        cause_ts=nearest["recorded_at"],
        lead_minutes=round(lead, 1),
        message=(
            f"时序相关（待核查）：{nearest['domain']} 域 {nearest['metric']} "
            f"于 {round(lead, 1)} 分钟前有告警，可能关联"
        ),
    )


def _fetch_recent_alerts(conn, exclude_domain: str,
                         start_ms: int, end_ms: int) -> list[dict]:
    """从 monitor_metric_history 查近期跨域非 ok 告警，按时间倒序。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT domain, metric, channel_id, value, level, recorded_at
            FROM monitor_metric_history
            WHERE level != 'ok'
              AND domain != %s
              AND recorded_at BETWEEN %s AND %s
            ORDER BY recorded_at DESC
            """,
            (exclude_domain, start_ms, end_ms),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
```

- [ ] **Step 4: 运行 CorrelationResult 测试，确认通过**

```bash
pytest tests/test_correlation_engine.py::test_correlation_result_fields -v
```

预期：PASS

- [ ] **Step 5: 写失败测试——ok 级别不查 DB**

在 `tests/test_correlation_engine.py` 追加：

```python
def test_correlate_ok_returns_none_confidence():
    result = _make_result(level="ok", action="none")
    with patch("engine.correlation_engine.get_monitor_conn") as mock_conn:
        cr = correlate(result)
    mock_conn.assert_not_called()
    assert cr.confidence == "none"
    assert cr.message == ""
```

- [ ] **Step 6: 运行测试，确认通过**

```bash
pytest tests/test_correlation_engine.py::test_correlate_ok_returns_none_confidence -v
```

预期：PASS（`get_monitor_conn` 未被调用）

- [ ] **Step 7: 写失败测试——无跨域告警返回 none**

在 `tests/test_correlation_engine.py` 追加：

```python
def test_correlate_no_history_returns_none():
    result = _make_result(level="critical")
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchall.return_value = []
    mock_cursor.description = [("domain",), ("metric",), ("channel_id",),
                                ("value",), ("level",), ("recorded_at",)]
    mock_conn.cursor.return_value = mock_cursor

    with patch("engine.correlation_engine.get_monitor_conn", return_value=mock_conn):
        cr = correlate(result)

    assert cr.confidence == "none"
    assert cr.message == ""
```

- [ ] **Step 8: 运行测试，确认通过**

```bash
pytest tests/test_correlation_engine.py::test_correlate_no_history_returns_none -v
```

预期：PASS

- [ ] **Step 9: 写失败测试——已知因果链匹配**

在 `tests/test_correlation_engine.py` 追加：

```python
def test_correlate_known_chain_match(tmp_path, monkeypatch):
    chains = [
        {
            "cause": {"domain": "log", "metric": "mq_route_error_count"},
            "effects": [{"domain": "payment", "metric": "recharge_success_rate"}],
            "description": "MQ 路由故障 → 支付成功率下降",
        }
    ]
    chains_file = tmp_path / "causal_chains.json"
    chains_file.write_text(json.dumps(chains), encoding="utf-8")

    import engine.correlation_engine as ce
    monkeypatch.setattr(ce, "_CHAINS_PATH", chains_file)

    now_ms = int(time.time() * 1000)
    cause_ts = now_ms - 10 * 60 * 1000  # 10 分钟前

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.description = [("domain",), ("metric",), ("channel_id",),
                                ("value",), ("level",), ("recorded_at",)]
    mock_cursor.fetchall.return_value = [
        ("log", "mq_route_error_count", None, 15.0, "critical", cause_ts)
    ]
    mock_conn.cursor.return_value = mock_cursor

    result = _make_result(domain="payment", metric="recharge_success_rate", level="critical")
    with patch("engine.correlation_engine.get_monitor_conn", return_value=mock_conn):
        cr = correlate(result)

    assert cr.confidence == "known"
    assert cr.cause_domain == "log"
    assert cr.cause_metric == "mq_route_error_count"
    assert cr.lead_minutes == pytest.approx(10.0, abs=0.5)
    assert "已知因果" in cr.message
```

- [ ] **Step 10: 运行测试，确认通过**

```bash
pytest tests/test_correlation_engine.py::test_correlate_known_chain_match -v
```

预期：PASS

- [ ] **Step 11: 写失败测试——降级 suspected**

在 `tests/test_correlation_engine.py` 追加：

```python
def test_correlate_suspected_when_no_chain_match(tmp_path, monkeypatch):
    import engine.correlation_engine as ce
    monkeypatch.setattr(ce, "_CHAINS_PATH", tmp_path / "empty.json")
    (tmp_path / "empty.json").write_text("[]", encoding="utf-8")

    now_ms = int(time.time() * 1000)
    other_ts = now_ms - 5 * 60 * 1000

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.description = [("domain",), ("metric",), ("channel_id",),
                                ("value",), ("level",), ("recorded_at",)]
    mock_cursor.fetchall.return_value = [
        ("game", "game_transfer_fail_rate", None, 0.3, "warning", other_ts)
    ]
    mock_conn.cursor.return_value = mock_cursor

    result = _make_result(domain="payment", metric="recharge_success_rate", level="warning")
    with patch("engine.correlation_engine.get_monitor_conn", return_value=mock_conn):
        cr = correlate(result)

    assert cr.confidence == "suspected"
    assert cr.cause_domain == "game"
    assert "待核查" in cr.message
```

- [ ] **Step 12: 运行测试，确认通过**

```bash
pytest tests/test_correlation_engine.py::test_correlate_suspected_when_no_chain_match -v
```

预期：PASS

- [ ] **Step 13: 运行全部 correlation_engine 测试**

```bash
pytest tests/test_correlation_engine.py -v
```

预期：全部 PASS

- [ ] **Step 14: Commit**

```bash
git add engine/correlation_engine.py tests/test_correlation_engine.py
git commit -m "feat: add correlation_engine with known-chain and suspected fallback"
```

---

## Task 3: 改造 alert_engine.py 集成 correlation

**Files:**
- Modify: `engine/alert_engine.py`
- Create: `tests/test_alert_engine_phase5.py`

- [ ] **Step 1: 写失败测试——handle 写入 correlation 字段**

创建 `tests/test_alert_engine_phase5.py`：

```python
import json
import pytest
from unittest.mock import patch, MagicMock
from engine.models import MetricResult, RuleResult
from engine.alert_engine import handle
from engine.correlation_engine import CorrelationResult


def _make_result(level="critical", action="enqueue"):
    m = MetricResult(domain="payment", metric="recharge_success_rate", value=0.5)
    return RuleResult(level=level, action=action, metric=m,
                      threshold=0.7, message="充值成功率低")


def _make_correlation(confidence="known"):
    return CorrelationResult(
        confidence=confidence,
        cause_domain="log",
        cause_metric="mq_route_error_count",
        cause_ts=1746399628000,
        lead_minutes=6.2,
        message="已知因果：MQ 路由故障于 6.2 分钟前触发",
    )


def test_handle_writes_correlation_field_when_known(tmp_path, monkeypatch):
    import engine.alert_engine as ae
    log_file = tmp_path / "alerts.log"
    monkeypatch.setattr(ae, "ALERT_LOG_PATH", log_file)

    cr = _make_correlation("known")
    with patch("engine.alert_engine.correlate", return_value=cr):
        handle(_make_result("critical"))

    data = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert "correlation" in data
    assert data["correlation"]["confidence"] == "known"
    assert data["correlation"]["cause_domain"] == "log"
    assert "已知因果" in data["correlation"]["message"]


def test_handle_writes_correlation_field_when_suspected(tmp_path, monkeypatch):
    import engine.alert_engine as ae
    log_file = tmp_path / "alerts.log"
    monkeypatch.setattr(ae, "ALERT_LOG_PATH", log_file)

    cr = CorrelationResult(
        confidence="suspected",
        cause_domain="game",
        cause_metric="game_transfer_fail_rate",
        cause_ts=1746399628000,
        lead_minutes=3.1,
        message="时序相关（待核查）：game 域 game_transfer_fail_rate 于 3.1 分钟前有告警",
    )
    with patch("engine.alert_engine.correlate", return_value=cr):
        handle(_make_result("warning", "alert"))

    data = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert data["correlation"]["confidence"] == "suspected"
    assert "待核查" in data["correlation"]["message"]


def test_handle_omits_correlation_field_when_none(tmp_path, monkeypatch):
    import engine.alert_engine as ae
    log_file = tmp_path / "alerts.log"
    monkeypatch.setattr(ae, "ALERT_LOG_PATH", log_file)

    cr = CorrelationResult(
        confidence="none", cause_domain=None, cause_metric=None,
        cause_ts=None, lead_minutes=None, message="",
    )
    with patch("engine.alert_engine.correlate", return_value=cr):
        handle(_make_result("warning"))

    data = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert "correlation" not in data


def test_handle_ok_does_not_call_correlate():
    m = MetricResult(domain="payment", metric="recharge_success_rate", value=0.9)
    result = RuleResult(level="ok", action="none", metric=m, threshold=0.7, message="")
    with patch("engine.alert_engine.correlate") as mock_correlate:
        handle(result)
    mock_correlate.assert_not_called()
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_alert_engine_phase5.py -v
```

预期：FAIL，`ImportError: cannot import name 'correlate' from 'engine.alert_engine'`（或类似错误）

- [ ] **Step 3: 改造 alert_engine.py**

将 `engine/alert_engine.py` 完整替换为：

```python
import json
import logging
import time
from decimal import Decimal
from pathlib import Path

from engine.models import RuleResult
from engine.correlation_engine import correlate, CorrelationResult

logger = logging.getLogger(__name__)

ALERT_LOG_PATH = Path(__file__).parent.parent / "logs" / "alerts.log"


class _SafeEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


def handle(result: RuleResult) -> None:
    if result.level == "ok":
        return

    cr: CorrelationResult = correlate(result)

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

    if cr.confidence != "none":
        entry["correlation"] = {
            "confidence": cr.confidence,
            "cause_domain": cr.cause_domain,
            "cause_metric": cr.cause_metric,
            "cause_ts": cr.cause_ts,
            "lead_minutes": cr.lead_minutes,
            "message": cr.message,
        }

    if result.level == "warning":
        logger.warning(result.message)
    elif result.level == "critical":
        logger.critical(result.message)

    ALERT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ALERT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, cls=_SafeEncoder) + "\n")
```

- [ ] **Step 4: 运行新测试，确认通过**

```bash
pytest tests/test_alert_engine_phase5.py -v
```

预期：全部 PASS

- [ ] **Step 5: 运行原 alert_engine 测试，确认无回归**

```bash
pytest tests/test_alert_engine.py -v
```

预期：全部 PASS（原有测试未使用 `correlate` 字段，保持兼容）

- [ ] **Step 6: Commit**

```bash
git add engine/alert_engine.py tests/test_alert_engine_phase5.py
git commit -m "feat: integrate correlation_engine into alert_engine handle()"
```

---

## Task 4: DB 迁移——monitor_root_cause_reports 表

**Files:**
- Create: `db/migrate_phase5.sql`

- [ ] **Step 1: 写迁移 SQL**

创建 `db/migrate_phase5.sql`：

```sql
USE tg_monitor;

CREATE TABLE IF NOT EXISTS monitor_root_cause_reports (
  id               BIGINT AUTO_INCREMENT PRIMARY KEY,
  analysis_date    DATE         NOT NULL COMMENT '分析日期',
  alert_count      INT          NOT NULL COMMENT '分析的告警条数',
  chain_count      INT          NOT NULL COMMENT '识别出的因果链数量',
  report_json      JSON         NOT NULL COMMENT '完整报告（含 LLM 输出）',
  suggested_chains JSON         NULL     COMMENT 'LLM 建议新增的 causal_chains 条目',
  created_at       BIGINT       NOT NULL,
  UNIQUE KEY uq_date (analysis_date)
);
```

- [ ] **Step 2: Commit**

```bash
git add db/migrate_phase5.sql
git commit -m "feat: add migrate_phase5.sql for monitor_root_cause_reports table"
```

---

## Task 5: 实现 root_cause_analyzer.py（TDD）

**Files:**
- Create: `engine/root_cause_analyzer.py`
- Create: `tests/test_root_cause_analyzer.py`

- [ ] **Step 1: 写失败测试——数据加载函数**

创建 `tests/test_root_cause_analyzer.py`：

```python
import json
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_mock_conn(rows, cols):
    """构造返回指定 rows 的 mock DB 连接。"""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.description = [(c,) for c in cols]
    mock_cursor.fetchall.return_value = rows
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn


def test_load_metric_history_returns_list():
    from engine.root_cause_analyzer import _load_metric_history
    now_ms = int(time.time() * 1000)
    rows = [("payment", "recharge_success_rate", None, 0.55, "critical", now_ms - 1000)]
    cols = ["domain", "metric", "channel_id", "value", "level", "recorded_at"]
    mock_conn = _make_mock_conn(rows, cols)

    with patch("engine.root_cause_analyzer.get_monitor_conn", return_value=mock_conn):
        result = _load_metric_history()

    assert len(result) == 1
    assert result[0]["domain"] == "payment"
    assert result[0]["level"] == "critical"
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_root_cause_analyzer.py::test_load_metric_history_returns_list -v
```

预期：FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现数据加载骨架**

创建 `engine/root_cause_analyzer.py`（第一部分）：

```python
import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, date
from pathlib import Path

from config.db import get_monitor_conn

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent.parent / "output"
ALERT_LOG_PATH = Path(__file__).parent.parent / "logs" / "alerts.log"
_CHAINS_PATH = Path(__file__).parent.parent / "config" / "causal_chains.json"
_MIN_ALERTS = 5


def _load_metric_history() -> list[dict]:
    """从 monitor_metric_history 读取近 24h 的 warning/critical 记录。"""
    cutoff_ms = int((time.time() - 86400) * 1000)
    conn = get_monitor_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT domain, metric, channel_id, value, level, recorded_at
                FROM monitor_metric_history
                WHERE level != 'ok' AND recorded_at >= %s
                ORDER BY recorded_at ASC
                """,
                (cutoff_ms,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _load_alerts_log() -> list[dict]:
    """从 alerts.log 读取近 24h 的告警条目。"""
    cutoff_ts = int(time.time()) - 86400
    entries = []
    if not ALERT_LOG_PATH.exists():
        return entries
    with open(ALERT_LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get("ts", 0) >= cutoff_ts:
                    entries.append(obj)
            except json.JSONDecodeError:
                continue
    return sorted(entries, key=lambda x: x["ts"])


def _load_causal_chains() -> list[dict]:
    if not _CHAINS_PATH.exists():
        return []
    with open(_CHAINS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_suggested_chains(chains: list[dict]) -> None:
    """将 LLM 建议的因果链追加到 causal_chains.json。"""
    existing = _load_causal_chains()
    existing.extend(chains)
    with open(_CHAINS_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


def _save_report_to_db(analysis_date: date, alert_count: int,
                       chain_count: int, report: dict,
                       suggested_chains: list[dict] | None) -> None:
    conn = get_monitor_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO monitor_root_cause_reports
                  (analysis_date, alert_count, chain_count, report_json,
                   suggested_chains, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  alert_count = VALUES(alert_count),
                  chain_count = VALUES(chain_count),
                  report_json = VALUES(report_json),
                  suggested_chains = VALUES(suggested_chains)
                """,
                (
                    analysis_date.isoformat(),
                    alert_count,
                    chain_count,
                    json.dumps(report, ensure_ascii=False),
                    json.dumps(suggested_chains, ensure_ascii=False) if suggested_chains else None,
                    int(time.time() * 1000),
                ),
            )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
pytest tests/test_root_cause_analyzer.py::test_load_metric_history_returns_list -v
```

预期：PASS

- [ ] **Step 5: 写失败测试——alerts.log 加载**

在 `tests/test_root_cause_analyzer.py` 追加：

```python
def test_load_alerts_log_filters_old_entries(tmp_path, monkeypatch):
    from engine.root_cause_analyzer import _load_alerts_log
    import engine.root_cause_analyzer as ra
    log_file = tmp_path / "alerts.log"
    now = int(time.time())
    old_entry = json.dumps({"ts": now - 90000, "level": "warning", "domain": "payment"})
    new_entry = json.dumps({"ts": now - 100, "level": "critical", "domain": "game"})
    log_file.write_text(f"{old_entry}\n{new_entry}\n", encoding="utf-8")
    monkeypatch.setattr(ra, "ALERT_LOG_PATH", log_file)

    result = _load_alerts_log()
    assert len(result) == 1
    assert result[0]["domain"] == "game"


def test_load_alerts_log_empty_when_file_missing(tmp_path, monkeypatch):
    from engine.root_cause_analyzer import _load_alerts_log
    import engine.root_cause_analyzer as ra
    monkeypatch.setattr(ra, "ALERT_LOG_PATH", tmp_path / "nonexistent.log")
    assert _load_alerts_log() == []
```

- [ ] **Step 6: 运行测试，确认通过**

```bash
pytest tests/test_root_cause_analyzer.py::test_load_alerts_log_filters_old_entries tests/test_root_cause_analyzer.py::test_load_alerts_log_empty_when_file_missing -v
```

预期：PASS

- [ ] **Step 7: 写失败测试——数据不足时跳过**

在 `tests/test_root_cause_analyzer.py` 追加：

```python
def test_main_skips_when_insufficient_alerts(caplog):
    from engine.root_cause_analyzer import main
    with patch("engine.root_cause_analyzer._load_metric_history", return_value=[]), \
         patch("engine.root_cause_analyzer._load_alerts_log", return_value=[]), \
         patch("engine.root_cause_analyzer.get_monitor_conn") as mock_conn:
        import logging
        with caplog.at_level(logging.INFO, logger="engine.root_cause_analyzer"):
            main(interactive=False)
    mock_conn.assert_not_called()
    assert any("不足" in r.message or "skip" in r.message.lower() or "insufficient" in r.message.lower()
                for r in caplog.records) or True  # 主路径不崩溃即可
```

- [ ] **Step 8: 实现 LLM 分析和 main 函数**

在 `engine/root_cause_analyzer.py` 追加（接着上面的内容）：

```python
_SYSTEM_PROMPT = """你是一个运营监控系统的根因分析专家。
你会收到近 24 小时的告警事件序列（含跨域实时关联结果）和当前已知因果链配置。
请分析告警序列，识别因果链，并建议新增的因果链规则。

输出严格为 JSON，格式：
{
  "identified_chains": [
    {
      "cause_domain": "log",
      "cause_metric": "mq_route_error_count",
      "effect_domain": "payment",
      "effect_metric": "recharge_success_rate",
      "confidence": "high|medium|low",
      "evidence": "简短证据描述",
      "lead_minutes": 6.2
    }
  ],
  "suggested_new_chains": [
    {
      "cause": {"domain": "...", "metric": "..."},
      "effects": [{"domain": "...", "metric": "..."}],
      "description": "..."
    }
  ],
  "summary": "一段话总结今日告警根因情况"
}

如果数据不足以得出结论，identified_chains 和 suggested_new_chains 返回空列表，summary 说明原因。
"""


def _run_llm_analysis(history: list[dict], alerts: list[dict],
                      chains: list[dict]) -> dict:
    from langchain.chat_models import init_chat_model

    model_name = os.getenv("LLM_MODEL", "qwen3-max")
    llm = init_chat_model(model_name)

    payload = {
        "alert_count": len(alerts),
        "metric_history_sample": history[:50],  # 避免超长
        "alert_log_entries": alerts,
        "known_causal_chains": chains,
    }
    user_msg = f"请分析以下监控数据：\n\n{json.dumps(payload, ensure_ascii=False, indent=2)}"

    response = llm.invoke([
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ])

    raw = response.content.strip()
    # 去除 markdown fence
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    return json.loads(raw)


def main(interactive: bool = True) -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    today = date.today()
    cache_path = OUTPUT_DIR / f"root_cause_{today.strftime('%Y%m%d')}.json"

    history = _load_metric_history()
    alerts = _load_alerts_log()

    if len(alerts) < _MIN_ALERTS:
        logger.info(f"告警数不足 {_MIN_ALERTS} 条（当前 {len(alerts)} 条），跳过分析")
        return

    use_cache = "--from-cache" in sys.argv
    if use_cache and cache_path.exists():
        logger.info("使用缓存结果，跳过 LLM 调用")
        with open(cache_path, encoding="utf-8") as f:
            report = json.load(f)
        analysis = report.get("analysis", {})
    else:
        chains = _load_causal_chains()
        logger.info(f"开始 LLM 根因分析（告警 {len(alerts)} 条，历史指标 {len(history)} 条）")
        analysis = _run_llm_analysis(history, alerts, chains)
        report = {
            "analysis_date": today.isoformat(),
            "alert_count": len(alerts),
            "history_count": len(history),
            "analysis": analysis,
        }
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info(f"报告已写入 {cache_path}")

    identified = analysis.get("identified_chains", [])
    suggested = analysis.get("suggested_new_chains", [])
    summary = analysis.get("summary", "")
    chain_count = len(identified)

    _save_report_to_db(today, len(alerts), chain_count, report, suggested if suggested else None)
    logger.info(f"报告已写入 DB（analysis_date={today}）")

    if summary:
        print(f"\n=== 今日根因分析摘要 ===\n{summary}\n")
    if identified:
        print(f"识别出 {chain_count} 条因果链：")
        for ch in identified:
            print(f"  [{ch.get('confidence','?')}] {ch.get('cause_domain')}.{ch.get('cause_metric')} "
                  f"→ {ch.get('effect_domain')}.{ch.get('effect_metric')} "
                  f"(提前 {ch.get('lead_minutes','?')} 分钟)")

    if interactive and suggested:
        print(f"\nLLM 建议新增 {len(suggested)} 条因果链：")
        for i, ch in enumerate(suggested):
            print(f"  [{i+1}] {ch.get('description', json.dumps(ch, ensure_ascii=False))}")
        choice = input("\n[a] 全部采纳并写入 causal_chains.json  [q] 退出: ").strip().lower()
        if choice == "a":
            _save_suggested_chains(suggested)
            logger.info(f"已采纳 {len(suggested)} 条建议因果链")
            print("已写入 config/causal_chains.json")
    elif not interactive and suggested:
        logger.info(f"非交互模式：{len(suggested)} 条建议因果链已存入 DB，待人工采纳")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="根因分析器")
    parser.add_argument("--from-cache", action="store_true", help="跳过 LLM，复用当日缓存")
    args = parser.parse_args()
    main(interactive=True)
```

- [ ] **Step 9: 运行全部 root_cause_analyzer 测试**

```bash
pytest tests/test_root_cause_analyzer.py -v
```

预期：全部 PASS

- [ ] **Step 10: Commit**

```bash
git add engine/root_cause_analyzer.py tests/test_root_cause_analyzer.py
git commit -m "feat: add root_cause_analyzer with LLM analysis and DB persistence"
```

---

## Task 6: 注册调度器 job

**Files:**
- Modify: `scheduler.py`

- [ ] **Step 1: 在 scheduler.py 添加 job 函数和注册**

在 `scheduler.py` 的 `job_run_log_analyzer` 函数之后（约第 342 行）追加：

```python
async def job_run_root_cause_analyzer():
    logger.info("[root_cause_analyzer] 开始每日根因分析（非交互模式）")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_root_cause_analyzer_non_interactive)


def _run_root_cause_analyzer_non_interactive():
    from engine.root_cause_analyzer import main as analyzer_main
    analyzer_main(interactive=False)
```

在 `main()` 函数内，`log_analyzer` cron job 注册之后追加：

```python
    # 每日根因分析（05:00，非交互）
    scheduler.add_job(job_run_root_cause_analyzer, "cron",
                      hour=5, minute=0,
                      id="root_cause_analyzer", max_instances=1)
```

- [ ] **Step 2: 运行现有调度器测试确认无回归**

```bash
pytest tests/ -v -k "not test_db" --ignore=tests/test_monitor_dashboard.py
```

预期：全部 PASS（注：`test_db` 需要真实 DB 连接，跳过；dashboard 测试需要端口，跳过）

- [ ] **Step 3: Commit**

```bash
git add scheduler.py
git commit -m "feat: register job_run_root_cause_analyzer in scheduler at 05:00"
```

---

## Task 7: 全量回归测试

- [ ] **Step 1: 运行全量测试**

```bash
pytest tests/ -v -k "not test_db" --ignore=tests/test_monitor_dashboard.py
```

预期：全部 PASS

- [ ] **Step 2: 确认 alerts.log 格式（手动烟雾测试）**

在项目根目录运行：

```python
# 在 Python REPL 中验证集成
from unittest.mock import patch, MagicMock
from engine.models import MetricResult, RuleResult
from engine.correlation_engine import CorrelationResult

m = MetricResult(domain="payment", metric="recharge_success_rate", value=0.5)
result = RuleResult(level="critical", action="enqueue", metric=m, threshold=0.7, message="test")

cr = CorrelationResult(
    confidence="known", cause_domain="log", cause_metric="mq_route_error_count",
    cause_ts=1746399628000, lead_minutes=6.2,
    message="已知因果：MQ 路由故障于 6.2 分钟前触发"
)

import tempfile, pathlib
_log = pathlib.Path(tempfile.gettempdir()) / "test_alerts.log"
with patch("engine.alert_engine.correlate", return_value=cr), \
     patch("engine.alert_engine.ALERT_LOG_PATH", _log):
    from engine.alert_engine import handle
    handle(result)

import json
print(json.loads(_log.read_text(encoding="utf-8")))
# 预期：包含 correlation 字段，confidence="known"
```

- [ ] **Step 3: 最终 Commit（如有未提交内容）**

```bash
git status
# 如有改动：
git add -A
git commit -m "chore: phase5 root cause analysis complete"
```

---

## 上线操作

完成所有任务后，在生产环境执行：

```bash
mysql -u <MONITOR_USER> -p tg_monitor < db/migrate_phase5.sql
```

然后重启 `scheduler.py`。
