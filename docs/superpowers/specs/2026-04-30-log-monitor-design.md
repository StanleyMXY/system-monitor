# 日志监控接入设计文档（Phase 3 补充）

**日期：** 2026-04-30
**状态：** 已批准，待实现

---

## 背景

Phase 1-3 实现了基于数据库指标的实时监控（支付/游戏/风控/活动/账户/操作共 6 个域）。数据库监控存在固有延迟——指标需要在时间窗口内积累足够样本才能触发告警。日志监控通过直接查询 ES 中的应用日志，可以在 DB 指标反应之前更早发现问题（如游戏供应商宕机、MQ 路由断裂等）。

**ES 环境：**
- 地址：`ES_URL` 环境变量（测试：`http://8.212.158.149:9200`，无认证）
- 测试索引：`q6app-*`、`q6mw-*`、`q6playgame-*`、`q6supplier-*`
- 生产索引：`swanapp-*`、`swanmw-*`、`swanplaygame-*`、`swansupplier-*`
- 日志级别在 `message` 字段文本中（无独立 `log.level` 字段）

---

## 设计原则

日志监控作为第 7 个监控域（`domain="log"`），完全复用现有采集 → 规则 → 告警 → 看板流水线，不引入新架构概念。

---

## 1. 采集层（monitor/log_monitor.py）

函数签名：`check_xxx(es_client, thresholds: dict) -> list[MetricResult]`

用 `es_client` 替代 `conn`，其余模式与现有 monitor 完全一致。

### 高优先级（5 分钟轮询）

**`check_game_launch_error(es_client, thresholds)`**
- 索引：`q6app-*`（生产：`swanapp-*`）
- 匹配：`LaunchController` 且（`IOException` 或 `status=500`）
- 时间窗口：`thresholds["high_priority"]["check_interval_minutes"]` 分钟
- 返回：`MetricResult(domain="log", metric="game_launch_error_count", value=错误次数, extra={"sample": 最新错误第一行})`

**`check_mq_route_error(es_client, thresholds)`**
- 索引：`q6app-*` + `q6mw-*`
- 匹配：`PRECONDITION_FAILED` 且 `Cannot route message`
- 返回：`MetricResult(domain="log", metric="mq_route_error_count", value=错误次数, extra={"exchange": exchange名称})`

**`check_db_shard_error(es_client, thresholds)`**
- 索引：`q6playgame-*`
- 匹配：`ShardingSphereException`
- 返回：`MetricResult(domain="log", metric="db_shard_error_count", value=错误次数)`

### 低优先级（30 分钟轮询）

**`check_websocket_error(es_client, thresholds)`**
- 索引：`q6app-*`
- 匹配：`SocketServer` 且 `严重异常`
- 返回：`MetricResult(domain="log", metric="websocket_error_count", value=错误次数)`

**`check_db_duplicate_error(es_client, thresholds)`**
- 索引：`q6app-*`
- 匹配：`SQLIntegrityConstraintViolation`
- 返回：`MetricResult(domain="log", metric="db_duplicate_error_count", value=错误次数)`

---

## 2. 阈值配置（config/thresholds_log.json）

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

---

## 3. 规则层（engine/rule_engine.py）

新增 5 条规则：

| metric | warning 条件 | critical 条件 | action |
|---|---|---|---|
| `game_launch_error_count` | ≥ `count_warning`(3) | ≥ `count_critical`(10) | alert / enqueue |
| `mq_route_error_count` | ≥ `count_warning`(5) | ≥ `count_critical`(20) | alert / enqueue |
| `db_shard_error_count` | ≥ `count_warning`(1) | ≥ `count_critical`(5) | alert / enqueue |
| `websocket_error_count` | ≥ `count_warning`(20) | — | alert |
| `db_duplicate_error_count` | ≥ `count_warning`(10) | — | alert |

游戏启动失败和 MQ 路由失败达到 critical 级别触发 enqueue（严重业务影响），其余只 alert。

---

## 4. ES 连接配置（config/es.py）

```python
import os
from elasticsearch import Elasticsearch

def get_es_client() -> Elasticsearch:
    url = os.getenv("ES_URL", "http://8.212.158.149:9200")
    return Elasticsearch(url)
```

`.env` 文件中配置 `ES_URL`，不硬编码地址。

---

## 5. 调度层（scheduler.py）

新增 `_run_log_monitor(check_fn)` 包装函数（类比 `_run_monitor`），使用 `es_client` 替代 `conn`，domain 固定为 `"log"`。

新增 5 个 async job，注册到调度器：

| job id | 采集函数 | 间隔来源 |
|---|---|---|
| `log_game_launch_error` | `check_game_launch_error` | `high_priority.check_interval_minutes` |
| `log_mq_route_error` | `check_mq_route_error` | `high_priority.check_interval_minutes` |
| `log_db_shard_error` | `check_db_shard_error` | `high_priority.check_interval_minutes` |
| `log_websocket_error` | `check_websocket_error` | `low_priority.check_interval_minutes` |
| `log_db_duplicate_error` | `check_db_duplicate_error` | `low_priority.check_interval_minutes` |

---

## 6. 看板扩展（dashboard/monitor_dashboard.py）

`_DOMAIN_LABELS` 新增 `"log": "日志域"`，`_render()` 遍历列表追加 `"log"`，自动渲染第 7 个域卡片。

---

## 依赖

需要安装 `elasticsearch` Python 客户端：

```bash
pip install elasticsearch
```

---

## 文件清单

| 操作 | 文件 |
|---|---|
| 新建 | `monitor/log_monitor.py` |
| 新建 | `config/es.py` |
| 新建 | `config/thresholds_log.json` |
| 新建 | `tests/test_log_monitor.py` |
| 新建 | `tests/test_log_rules.py` |
| 修改 | `engine/rule_engine.py` |
| 修改 | `scheduler.py` |
| 修改 | `dashboard/monitor_dashboard.py` |
| 修改 | `.env` |
| 修改 | `requirements.txt`（如有） |
