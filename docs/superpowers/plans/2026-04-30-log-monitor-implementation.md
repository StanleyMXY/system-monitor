# 日志监控接入实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 ES 日志监控作为第 7 个域（domain="log"）接入现有监控流水线，新增 5 个采集函数、5 条规则、5 个调度 job，看板扩展到 7 域。

**Architecture:** 复用现有采集 → 规则 → 告警 → 看板流水线。`log_monitor.py` 用 `es_client` 替代 `conn`，其余模式与现有 monitor 完全一致。ES 地址通过 `config/es.py` 读取 `ES_URL` 环境变量。

**Tech Stack:** Python 3.11+, elasticsearch-py 8.x, APScheduler 3.x, pytest

---

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `config/es.py` | ES 客户端工厂 |
| 新建 | `config/thresholds_log.json` | 日志域阈值配置 |
| 新建 | `monitor/log_monitor.py` | 5 个采集函数 |
| 新建 | `tests/test_log_monitor.py` | 采集函数单元测试 |
| 新建 | `tests/test_log_rules.py` | 规则单元测试 |
| 修改 | `engine/rule_engine.py` | 新增 5 条规则 |
| 修改 | `scheduler.py` | 新增 _run_log_monitor + 5 个 job |
| 修改 | `dashboard/monitor_dashboard.py` | 扩展到 7 域 |
| 修改 | `.env` | 新增 ES_URL |

---

## Task 1: 安装依赖 + 配置 ES 客户端

**Files:**
- Modify: `.env`
- Create: `config/es.py`

- [ ] **Step 1: 安装 elasticsearch-py**

```bash
pip install "elasticsearch>=8.0.0,<9.0.0"
```

Expected: 安装成功，`import elasticsearch` 无报错

- [ ] **Step 2: 在 .env 追加 ES_URL**

在 `.env` 文件末尾追加：

```
ES_URL=http://8.212.158.149:9200
ES_INDEX_PREFIX=q6
```

`ES_INDEX_PREFIX` 控制用测试环境（`q6`）还是生产（`swan`），不需要改代码切换环境。

- [ ] **Step 3: 新建 config/es.py**

```python
import os
from elasticsearch import Elasticsearch


def get_es_client() -> Elasticsearch:
    url = os.getenv("ES_URL", "http://8.212.158.149:9200")
    return Elasticsearch(url)


def get_index_prefix() -> str:
    return os.getenv("ES_INDEX_PREFIX", "q6")
```

- [ ] **Step 4: 验证连接**

```bash
python -c "
from config.es import get_es_client
es = get_es_client()
info = es.info()
print('ES 连接成功:', info['version']['number'])
"
```

Expected: `ES 连接成功: 8.x.x`

- [ ] **Step 5: Commit**

```bash
git add config/es.py .env
git commit -m "feat: add ES client config (config/es.py)"
```

---

## Task 2: 阈值配置文件

**Files:**
- Create: `config/thresholds_log.json`

- [ ] **Step 1: 新建 config/thresholds_log.json**

```json
{
  "_comment": "日志监控阈值，count 单位：次，interval 单位：分钟",
  "high_priority": {
    "check_interval_minutes": 5,
    "game_launch_error": {
      "count_warning": 3,
      "count_critical": 10
    },
    "mq_route_error": {
      "count_warning": 5,
      "count_critical": 20
    },
    "db_shard_error": {
      "count_warning": 1,
      "count_critical": 5
    }
  },
  "low_priority": {
    "check_interval_minutes": 30,
    "websocket_error": {
      "count_warning": 20
    },
    "db_duplicate_error": {
      "count_warning": 10
    }
  }
}
```

- [ ] **Step 2: 验证 threshold_config 能加载**

```bash
python -c "
from engine.threshold_config import load_thresholds
t = load_thresholds('log')
print('high_priority interval:', t['high_priority']['check_interval_minutes'])
print('game_launch_error warning:', t['high_priority']['game_launch_error']['count_warning'])
"
```

Expected: `high_priority interval: 5` / `game_launch_error warning: 3`

- [ ] **Step 3: Commit**

```bash
git add config/thresholds_log.json
git commit -m "config: add thresholds_log.json for log monitor domain"
```

---

## Task 3: 实现 log_monitor.py（TDD）

**Files:**
- Create: `monitor/log_monitor.py`
- Create: `tests/test_log_monitor.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_log_monitor.py`：

```python
from unittest.mock import MagicMock, patch
from monitor.log_monitor import (
    check_game_launch_error,
    check_mq_route_error,
    check_db_shard_error,
    check_websocket_error,
    check_db_duplicate_error,
)

THRESHOLDS = {
    "high_priority": {
        "check_interval_minutes": 5,
        "game_launch_error": {"count_warning": 3, "count_critical": 10},
        "mq_route_error": {"count_warning": 5, "count_critical": 20},
        "db_shard_error": {"count_warning": 1, "count_critical": 5},
    },
    "low_priority": {
        "check_interval_minutes": 30,
        "websocket_error": {"count_warning": 20},
        "db_duplicate_error": {"count_warning": 10},
    },
}


def _make_es(count, sample_message=""):
    es = MagicMock()
    es.count.return_value = {"count": count}
    es.search.return_value = {
        "hits": {
            "hits": [{"_source": {"message": sample_message}}] if sample_message else []
        }
    }
    return es


def test_check_game_launch_error_returns_one_metric():
    es = _make_es(5, "LaunchController: 启动游戏 xxx\njava.io.IOException: 500")
    results = check_game_launch_error(es, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "game_launch_error_count"
    assert results[0].domain == "log"
    assert results[0].value == 5.0


def test_check_game_launch_error_zero():
    es = _make_es(0)
    results = check_game_launch_error(es, THRESHOLDS)
    assert results[0].value == 0.0


def test_check_mq_route_error_returns_one_metric():
    es = _make_es(12)
    results = check_mq_route_error(es, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "mq_route_error_count"
    assert results[0].value == 12.0


def test_check_db_shard_error_returns_one_metric():
    es = _make_es(2)
    results = check_db_shard_error(es, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "db_shard_error_count"
    assert results[0].value == 2.0


def test_check_websocket_error_returns_one_metric():
    es = _make_es(25)
    results = check_websocket_error(es, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "websocket_error_count"
    assert results[0].value == 25.0


def test_check_db_duplicate_error_returns_one_metric():
    es = _make_es(3)
    results = check_db_duplicate_error(es, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "db_duplicate_error_count"
    assert results[0].value == 3.0


def test_game_launch_error_extra_has_sample():
    es = _make_es(1, "LaunchController: 启动游戏 abc\njava.io.IOException")
    results = check_game_launch_error(es, THRESHOLDS)
    assert "sample" in results[0].extra
```

- [ ] **Step 2: 运行确认测试失败**

```bash
cd "D:\Projects\PycharmProjects\System Monitor" && python -m pytest tests/test_log_monitor.py -v
```

Expected: `ImportError`（文件尚未创建）

- [ ] **Step 3: 实现 monitor/log_monitor.py**

```python
import os
import time
from engine.models import MetricResult


def _get_prefix() -> str:
    return os.getenv("ES_INDEX_PREFIX", "q6")


def _count_errors(es, index_pattern: str, must_phrases: list[str], window_minutes: int) -> tuple[int, str]:
    """查询 ES 指定时间窗口内匹配所有短语的日志数量，返回 (count, 最新一条消息)。"""
    cutoff = f"now-{window_minutes}m"
    must = [{"match_phrase": {"message": p}} for p in must_phrases]
    must.append({"range": {"@timestamp": {"gte": cutoff}}})

    count_resp = es.count(index=index_pattern, body={"query": {"bool": {"must": must}}})
    count = count_resp["count"]

    sample = ""
    if count > 0:
        search_resp = es.search(
            index=index_pattern,
            body={
                "query": {"bool": {"must": must}},
                "size": 1,
                "sort": [{"@timestamp": {"order": "desc"}}],
                "_source": ["message"],
            },
        )
        hits = search_resp["hits"]["hits"]
        if hits:
            sample = hits[0]["_source"]["message"].split("\n")[0][:200]

    return count, sample


def check_game_launch_error(es, thresholds: dict) -> list[MetricResult]:
    """采集游戏启动失败次数（LaunchController IOException/500）。"""
    cfg = thresholds["high_priority"]
    prefix = _get_prefix()
    count, sample = _count_errors(
        es,
        f"{prefix}app-*",
        ["LaunchController", "IOException"],
        cfg["check_interval_minutes"],
    )
    return [MetricResult(
        domain="log", metric="game_launch_error_count",
        value=float(count),
        extra={"sample": sample},
    )]


def check_mq_route_error(es, thresholds: dict) -> list[MetricResult]:
    """采集 MQ exchange 路由失败次数（PRECONDITION_FAILED）。"""
    cfg = thresholds["high_priority"]
    prefix = _get_prefix()
    count, _ = _count_errors(
        es,
        f"{prefix}app-*,{prefix}mw-*",
        ["PRECONDITION_FAILED", "Cannot route message"],
        cfg["check_interval_minutes"],
    )
    return [MetricResult(
        domain="log", metric="mq_route_error_count",
        value=float(count),
    )]


def check_db_shard_error(es, thresholds: dict) -> list[MetricResult]:
    """采集 ShardingSphere 分表路由失败次数。"""
    cfg = thresholds["high_priority"]
    prefix = _get_prefix()
    count, _ = _count_errors(
        es,
        f"{prefix}playgame-*",
        ["ShardingSphereException"],
        cfg["check_interval_minutes"],
    )
    return [MetricResult(
        domain="log", metric="db_shard_error_count",
        value=float(count),
    )]


def check_websocket_error(es, thresholds: dict) -> list[MetricResult]:
    """采集 WebSocket 严重异常次数。"""
    cfg = thresholds["low_priority"]
    prefix = _get_prefix()
    count, _ = _count_errors(
        es,
        f"{prefix}app-*",
        ["SocketServer", "严重异常"],
        cfg["check_interval_minutes"],
    )
    return [MetricResult(
        domain="log", metric="websocket_error_count",
        value=float(count),
    )]


def check_db_duplicate_error(es, thresholds: dict) -> list[MetricResult]:
    """采集数据库唯一键冲突次数。"""
    cfg = thresholds["low_priority"]
    prefix = _get_prefix()
    count, _ = _count_errors(
        es,
        f"{prefix}app-*",
        ["SQLIntegrityConstraintViolation"],
        cfg["check_interval_minutes"],
    )
    return [MetricResult(
        domain="log", metric="db_duplicate_error_count",
        value=float(count),
    )]
```

- [ ] **Step 4: 运行确认测试通过**

```bash
python -m pytest tests/test_log_monitor.py -v
```

Expected: 7/7 PASS

- [ ] **Step 5: Commit**

```bash
git add monitor/log_monitor.py tests/test_log_monitor.py
git commit -m "feat: add log_monitor (5 check functions for ES log domain)"
```

---

## Task 4: 扩展 rule_engine.py — 新增 5 条规则（TDD）

**Files:**
- Modify: `engine/rule_engine.py`
- Create: `tests/test_log_rules.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_log_rules.py`：

```python
from engine.models import MetricResult
from engine.rule_engine import evaluate

THRESHOLDS = {
    "high_priority": {
        "game_launch_error": {"count_warning": 3, "count_critical": 10},
        "mq_route_error": {"count_warning": 5, "count_critical": 20},
        "db_shard_error": {"count_warning": 1, "count_critical": 5},
    },
    "low_priority": {
        "websocket_error": {"count_warning": 20},
        "db_duplicate_error": {"count_warning": 10},
    },
}


def _m(metric, value, extra=None):
    return MetricResult(domain="log", metric=metric, value=value, extra=extra or {})


# game_launch_error
def test_game_launch_error_ok():
    result = evaluate([_m("game_launch_error_count", 2.0)], THRESHOLDS)[0]
    assert result.level == "ok"

def test_game_launch_error_warning():
    result = evaluate([_m("game_launch_error_count", 3.0)], THRESHOLDS)[0]
    assert result.level == "warning"
    assert result.action == "alert"

def test_game_launch_error_critical():
    result = evaluate([_m("game_launch_error_count", 10.0)], THRESHOLDS)[0]
    assert result.level == "critical"
    assert result.action == "enqueue"

# mq_route_error
def test_mq_route_error_ok():
    result = evaluate([_m("mq_route_error_count", 4.0)], THRESHOLDS)[0]
    assert result.level == "ok"

def test_mq_route_error_warning():
    result = evaluate([_m("mq_route_error_count", 5.0)], THRESHOLDS)[0]
    assert result.level == "warning"
    assert result.action == "alert"

def test_mq_route_error_critical():
    result = evaluate([_m("mq_route_error_count", 20.0)], THRESHOLDS)[0]
    assert result.level == "critical"
    assert result.action == "enqueue"

# db_shard_error
def test_db_shard_error_ok():
    result = evaluate([_m("db_shard_error_count", 0.0)], THRESHOLDS)[0]
    assert result.level == "ok"

def test_db_shard_error_warning():
    result = evaluate([_m("db_shard_error_count", 1.0)], THRESHOLDS)[0]
    assert result.level == "warning"
    assert result.action == "alert"

def test_db_shard_error_critical():
    result = evaluate([_m("db_shard_error_count", 5.0)], THRESHOLDS)[0]
    assert result.level == "critical"
    assert result.action == "enqueue"

# websocket_error
def test_websocket_error_ok():
    result = evaluate([_m("websocket_error_count", 19.0)], THRESHOLDS)[0]
    assert result.level == "ok"

def test_websocket_error_warning():
    result = evaluate([_m("websocket_error_count", 20.0)], THRESHOLDS)[0]
    assert result.level == "warning"
    assert result.action == "alert"

# db_duplicate_error
def test_db_duplicate_error_ok():
    result = evaluate([_m("db_duplicate_error_count", 9.0)], THRESHOLDS)[0]
    assert result.level == "ok"

def test_db_duplicate_error_warning():
    result = evaluate([_m("db_duplicate_error_count", 10.0)], THRESHOLDS)[0]
    assert result.level == "warning"
    assert result.action == "alert"
```

- [ ] **Step 2: 运行确认测试失败**

```bash
python -m pytest tests/test_log_rules.py -v
```

Expected: 多数 FAIL（规则未注册）

- [ ] **Step 3: 在 rule_engine.py 追加 5 条规则函数**

在 `engine/rule_engine.py` 中，在 `_RULES = {` 行之前追加：

```python
# --- log domain rules ---

def _log_game_launch_error(m: MetricResult, t: dict) -> RuleResult:
    cfg = t["high_priority"]["game_launch_error"]
    if m.value >= cfg["count_critical"]:
        sample = m.extra.get("sample", "")
        return RuleResult(
            level="critical", action="enqueue", metric=m,
            threshold=cfg["count_critical"],
            message=f"游戏启动严重异常: {int(m.value)} 次 >= {cfg['count_critical']} 次"
                    + (f" | {sample}" if sample else ""),
        )
    if m.value >= cfg["count_warning"]:
        return RuleResult(
            level="warning", action="alert", metric=m,
            threshold=cfg["count_warning"],
            message=f"游戏启动异常偏高: {int(m.value)} 次 >= {cfg['count_warning']} 次",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=cfg["count_warning"], message="")


def _log_mq_route_error(m: MetricResult, t: dict) -> RuleResult:
    cfg = t["high_priority"]["mq_route_error"]
    if m.value >= cfg["count_critical"]:
        return RuleResult(
            level="critical", action="enqueue", metric=m,
            threshold=cfg["count_critical"],
            message=f"MQ 路由严重故障: {int(m.value)} 次 >= {cfg['count_critical']} 次",
        )
    if m.value >= cfg["count_warning"]:
        return RuleResult(
            level="warning", action="alert", metric=m,
            threshold=cfg["count_warning"],
            message=f"MQ 路由失败偏高: {int(m.value)} 次 >= {cfg['count_warning']} 次",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=cfg["count_warning"], message="")


def _log_db_shard_error(m: MetricResult, t: dict) -> RuleResult:
    cfg = t["high_priority"]["db_shard_error"]
    if m.value >= cfg["count_critical"]:
        return RuleResult(
            level="critical", action="enqueue", metric=m,
            threshold=cfg["count_critical"],
            message=f"分表路由严重故障: {int(m.value)} 次 >= {cfg['count_critical']} 次",
        )
    if m.value >= cfg["count_warning"]:
        return RuleResult(
            level="warning", action="alert", metric=m,
            threshold=cfg["count_warning"],
            message=f"分表路由失败: {int(m.value)} 次 >= {cfg['count_warning']} 次",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=cfg["count_warning"], message="")


def _log_websocket_error(m: MetricResult, t: dict) -> RuleResult:
    threshold = t["low_priority"]["websocket_error"]["count_warning"]
    if m.value >= threshold:
        return RuleResult(
            level="warning", action="alert", metric=m, threshold=threshold,
            message=f"WebSocket 异常偏高: {int(m.value)} 次 >= {threshold} 次",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=threshold, message="")


def _log_db_duplicate_error(m: MetricResult, t: dict) -> RuleResult:
    threshold = t["low_priority"]["db_duplicate_error"]["count_warning"]
    if m.value >= threshold:
        return RuleResult(
            level="warning", action="alert", metric=m, threshold=threshold,
            message=f"DB 唯一键冲突偏高: {int(m.value)} 次 >= {threshold} 次",
        )
    return RuleResult(level="ok", action="none", metric=m, threshold=threshold, message="")
```

- [ ] **Step 4: 在 _RULES dict 末尾追加注册**

```python
    # log domain
    "game_launch_error_count": _log_game_launch_error,
    "mq_route_error_count": _log_mq_route_error,
    "db_shard_error_count": _log_db_shard_error,
    "websocket_error_count": _log_websocket_error,
    "db_duplicate_error_count": _log_db_duplicate_error,
```

- [ ] **Step 5: 运行确认测试通过**

```bash
python -m pytest tests/test_log_rules.py -v
```

Expected: 14/14 PASS

- [ ] **Step 6: Commit**

```bash
git add engine/rule_engine.py tests/test_log_rules.py
git commit -m "feat: add log domain rules (5 rules for ES log monitor)"
```

---

## Task 5: 扩展 scheduler.py — 新增 _run_log_monitor + 5 个 job

**Files:**
- Modify: `scheduler.py`

- [ ] **Step 1: 在 scheduler.py 顶部 import 区追加**

在现有 `from monitor.operation_monitor import (...)` 之后追加：

```python
from config.es import get_es_client
from monitor.log_monitor import (
    check_game_launch_error,
    check_mq_route_error,
    check_db_shard_error,
    check_websocket_error,
    check_db_duplicate_error,
)
```

- [ ] **Step 2: 在 _run_monitor 函数之后追加 _run_log_monitor**

在 `scheduler.py` 的 `_run_monitor` 函数结束后（`# --- payment jobs ---` 之前）追加：

```python
def _run_log_monitor(check_fn):
    """执行日志采集函数的完整流程：采集 → 评估 → 告警 → 入队 → 更新看板。"""
    thresholds = load_thresholds("log")
    es = get_es_client()

    metrics = check_fn(es, thresholds)
    rule_results = evaluate(metrics, thresholds)

    for result in rule_results:
        if result.level == "ok":
            continue
        try:
            handle(result)
            if result.action == "enqueue":
                enqueue_action(
                    domain=result.metric.domain,
                    action_type="log_alert",
                    target_id=None,
                    payload={
                        "metric": result.metric.metric,
                        "value": float(result.metric.value),
                        "extra": result.metric.extra,
                    },
                    priority=1 if result.level == "critical" else 2,
                    triggered_by=result.message,
                    metric_value=result.metric.value,
                    threshold=result.threshold,
                )
        except Exception as exc:
            logger.error(f"处理日志告警结果异常 [{result.metric.metric}]: {exc}", exc_info=True)

    try:
        dashboard.update("log", rule_results)
    except Exception as exc:
        logger.error(f"看板更新异常 [log]: {exc}", exc_info=True)
```

- [ ] **Step 3: 在 operation jobs 之后追加 5 个 async job 函数**

在 `job_check_config_change` 函数之后、`_on_job_error` 之前追加：

```python
# --- log jobs ---

async def job_check_game_launch_error():
    logger.info("[log] 执行游戏启动异常监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_log_monitor, check_game_launch_error)


async def job_check_mq_route_error():
    logger.info("[log] 执行 MQ 路由错误监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_log_monitor, check_mq_route_error)


async def job_check_db_shard_error():
    logger.info("[log] 执行分表路由错误监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_log_monitor, check_db_shard_error)


async def job_check_websocket_error():
    logger.info("[log] 执行 WebSocket 异常监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_log_monitor, check_websocket_error)


async def job_check_db_duplicate_error():
    logger.info("[log] 执行 DB 唯一键冲突监控")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_log_monitor, check_db_duplicate_error)
```

- [ ] **Step 4: 在 main() 函数内加载配置并注册 5 个 job**

在 `main()` 函数中，`operation_cfg = load_thresholds("operation")` 之后追加：

```python
    log_cfg = load_thresholds("log")
```

在 operation job 注册区块之后追加：

```python
    # log (high priority)
    scheduler.add_job(job_check_game_launch_error, "interval",
                      minutes=log_cfg["high_priority"]["check_interval_minutes"],
                      id="log_game_launch_error", max_instances=1)
    scheduler.add_job(job_check_mq_route_error, "interval",
                      minutes=log_cfg["high_priority"]["check_interval_minutes"],
                      id="log_mq_route_error", max_instances=1)
    scheduler.add_job(job_check_db_shard_error, "interval",
                      minutes=log_cfg["high_priority"]["check_interval_minutes"],
                      id="log_db_shard_error", max_instances=1)

    # log (low priority)
    scheduler.add_job(job_check_websocket_error, "interval",
                      minutes=log_cfg["low_priority"]["check_interval_minutes"],
                      id="log_websocket_error", max_instances=1)
    scheduler.add_job(job_check_db_duplicate_error, "interval",
                      minutes=log_cfg["low_priority"]["check_interval_minutes"],
                      id="log_db_duplicate_error", max_instances=1)
```

- [ ] **Step 5: 验证 scheduler.py 导入无报错**

```bash
python -c "import scheduler; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add scheduler.py
git commit -m "feat: register log domain jobs in scheduler (5 ES log monitor jobs)"
```

---

## Task 6: 扩展 dashboard — 新增日志域卡片

**Files:**
- Modify: `dashboard/monitor_dashboard.py`

- [ ] **Step 1: 更新 _DOMAIN_LABELS**

将 `_DOMAIN_LABELS` 替换为：

```python
_DOMAIN_LABELS = {
    "payment": "支付域",
    "game": "游戏供应商域",
    "risk": "风控域",
    "activity": "活动域",
    "account": "账户域",
    "operation": "系统操作域",
    "log": "日志域",
}
```

- [ ] **Step 2: 更新 _render() 中的域遍历列表**

将：

```python
        for domain_key in ["payment", "game", "risk", "activity", "account", "operation"]:
```

替换为：

```python
        for domain_key in ["payment", "game", "risk", "activity", "account", "operation", "log"]:
```

- [ ] **Step 3: 验证看板渲染正常**

```bash
python -c "
from dashboard.monitor_dashboard import MonitorDashboard
d = MonitorDashboard(port=0)
d.start()
d.update('log', [])
print('render OK, domains:', list(d.domain_results.keys()))
"
```

Expected: `render OK, domains: ['log']`

- [ ] **Step 4: Commit**

```bash
git add dashboard/monitor_dashboard.py
git commit -m "feat: extend dashboard to 7 domains (add log domain)"
```

---

## Task 7: 全量测试 + smoke test

- [ ] **Step 1: 运行所有单元测试**

```bash
python -m pytest tests/ -v
```

Expected: 全部 PASS（现有 81 条 + 新增 21 条 = 102 条）

- [ ] **Step 2: smoke test — 验证 ES 查询可跑通**

```bash
python -c "
from config.es import get_es_client
from engine.threshold_config import load_thresholds
from monitor.log_monitor import (
    check_game_launch_error, check_mq_route_error,
    check_db_shard_error, check_websocket_error, check_db_duplicate_error,
)

es = get_es_client()
t = load_thresholds('log')
checks = [
    check_game_launch_error,
    check_mq_route_error,
    check_db_shard_error,
    check_websocket_error,
    check_db_duplicate_error,
]
for fn in checks:
    results = fn(es, t)
    print(f'✅ {fn.__name__}: {[f\"{r.metric}={r.value}\" for r in results]}')
"
```

Expected: 5 行 ✅，无报错

- [ ] **Step 3: 验证 scheduler 导入正常**

```bash
python -c "import scheduler; print('scheduler import OK')"
```

Expected: `scheduler import OK`

- [ ] **Step 4: 最终 commit**

```bash
git add -A
git commit -m "chore: log monitor complete - ES log domain integrated"
```
