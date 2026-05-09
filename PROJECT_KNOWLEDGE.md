# 天工平台系统运营监控 — Project Knowledge

> 本文档是本仓库的完整技术参考，面向新接手开发者或跨会话的 AI 协作者。

---

## 1. 项目定位

**天工平台实时系统监控**——对天工平台（tian-gong）生产库进行周期性 SQL 采集，经规则引擎评估后输出告警和动作队列，覆盖支付、游戏供应商、风控、活动、账户、系统操作、日志七个域，常驻运行。AI 分析器（阈值优化器、日志分析器、根因分析器）定期离线分析，建议通过 Web 审批落地。

**与 TG-Ads-Analysis 的关系：** TG-Ads-Analysis（兄弟项目）是离线分析系统，每日批跑 ETL + AI 日报，面向运营决策。本项目是其衍生独立系统，架构与之解耦（独立库、独立调度、独立告警），但继承了读写分库、APScheduler 异步调度、PyMySQL 技术栈等方法论。

---

## 2. 完整文件结构

```
System Monitor/
├── config/
│   ├── __init__.py
│   ├── db.py                       # 数据库连接工厂（读写分库）
│   ├── es.py                       # ES 连接工厂
│   ├── thresholds_payment.json     # 支付域阈值配置
│   ├── thresholds_game.json
│   ├── thresholds_risk.json
│   ├── thresholds_activity.json
│   ├── thresholds_account.json
│   ├── thresholds_operation.json
│   ├── thresholds_log.json
│   ├── noise_rules.json            # 已确认日志噪音规则（运行时加载）
│   └── causal_chains.json          # 已知因果链配置（实时根因关联使用）
│
├── engine/
│   ├── __init__.py
│   ├── models.py                   # 核心数据结构（MetricResult / RuleResult）
│   ├── threshold_config.py         # 阈值 JSON 加载器 + METRIC_THRESHOLD_MAP
│   ├── rule_engine.py              # 规则引擎（19 条规则，全域覆盖）
│   ├── alert_engine.py             # 告警引擎（日志 + logs/alerts.log）
│   ├── correlation_engine.py       # 实时根因关联（per-alert，best-effort）
│   ├── threshold_optimizer.py      # AI 阈值优化器（每周日 02:00）
│   ├── log_analyzer.py             # AI 日志智能分析器（每天 04:00）
│   ├── root_cause_analyzer.py      # AI 根因分析器（每天 05:00）
│   └── suggestions_store.py        # monitor_suggestions 表读写
│
├── monitor/
│   ├── __init__.py
│   ├── payment_monitor.py
│   ├── game_monitor.py
│   ├── risk_monitor.py
│   ├── activity_monitor.py
│   ├── account_monitor.py
│   ├── operation_monitor.py
│   └── log_monitor.py              # ES 日志域采集
│
├── notify/
│   ├── __init__.py
│   ├── tg_client.py                # TG Bot API 底层（send_alert）
│   └── alerts.py                   # 业务告警封装（notify_watchdog_alert）
│
├── scripts/
│   └── watchdog.py                 # 心跳看门狗，系统 cron 每 10 分钟调用
│
├── executor/
│   ├── __init__.py
│   └── action_executor.py          # 执行层：写 tg_monitor.monitor_action_queue
│
├── api/
│   ├── __init__.py
│   ├── server.py                   # FastAPI app 入口（端口 8080）
│   ├── routes/
│   │   ├── __init__.py
│   │   └── suggestions.py          # GET/POST /api/suggestions 路由
│   └── actions/
│       ├── __init__.py
│       ├── threshold.py            # 采纳阈值建议：写 thresholds_*.json
│       ├── noise.py                # 采纳噪音规则：写 noise_rules.json
│       └── chain.py                # 采纳因果链：写 causal_chains.json
│
├── dashboard/
│   ├── __init__.py
│   └── monitor_dashboard.py        # 看板 HTML 生成（scheduler 进程内，不含 HTTP）
│
├── db/
│   ├── init_monitor.sql            # 建库 + 基础表
│   ├── migrate_phase4.sql          # monitor_metric_history
│   ├── migrate_log_analyzer.sql    # monitor_noise_rules
│   ├── migrate_phase5.sql          # monitor_root_cause_reports
│   ├── migrate_suggestions.sql     # monitor_suggestions
│   └── migrate_heartbeat.sql       # scheduler_heartbeat（心跳表）
│
├── tests/
│   ├── __init__.py
│   ├── test_db.py
│   ├── test_threshold_config.py
│   ├── test_rule_engine.py
│   ├── test_rule_engine_phase2.py
│   ├── test_phase3_rules.py
│   ├── test_alert_engine.py
│   ├── test_alert_engine_phase5.py
│   ├── test_payment_monitor.py
│   ├── test_game_monitor.py
│   ├── test_risk_monitor.py
│   ├── test_activity_monitor.py
│   ├── test_account_monitor.py
│   ├── test_operation_monitor.py
│   ├── test_action_executor.py
│   ├── test_log_monitor.py
│   ├── test_log_rules.py
│   ├── test_monitor_dashboard.py
│   ├── test_threshold_optimizer.py
│   ├── test_metric_history_writer.py
│   ├── test_log_analyzer.py
│   ├── test_correlation_engine.py
│   ├── test_root_cause_analyzer.py
│   ├── test_suggestions_store.py
│   ├── test_api_actions.py
│   └── test_api_suggestions.py
│
├── docs/superpowers/
│   ├── specs/2026-05-08-suggestions-approval-design.md
│   └── plans/2026-05-08-suggestions-approval.md
│
├── output/
│   ├── monitor_dashboard.html      # 看板（scheduler 生成，api/server 服务）
│   └── suggestions_detail.html    # 审批页（交互式，api/server 服务）
│
├── logs/
│   └── alerts.log                  # 结构化告警日志（JSON Lines）
│
├── scheduler.py                    # 主入口：APScheduler 常驻调度器（不含 HTTP server）
├── .env                            # 本地环境变量（不入库）
└── .env.example                    # 环境变量模板
```

---

## 3. 启动方式

```bash
# 调度器（常驻，负责采集/告警/写 HTML，port=0 不启动 HTTP）
python scheduler.py

# API server（FastAPI，端口 8080，serve output/ 静态文件 + 审批 REST API）
python -m api.server
```

看板：http://localhost:8080/monitor_dashboard.html  
审批：http://localhost:8080/suggestions_detail.html  
API 文档：http://localhost:8080/docs

---

## 4. 数据库连接配置

读写分库，通过 `.env` 配置两组独立连接。

### 环境变量（.env）

```ini
# 源库（只读）—— tian-gong 生产库
SOURCE_HOST=127.0.0.1
SOURCE_PORT=3306
SOURCE_USER=your_user
SOURCE_PASSWORD=your_password
SOURCE_DATABASE=tian-gong

# 监控库（读写）—— tg_monitor，需单独创建
MONITOR_HOST=127.0.0.1
MONITOR_PORT=3306
MONITOR_USER=your_user
MONITOR_PASSWORD=your_password
MONITOR_DATABASE=tg_monitor

# ES
ES_URL=http://8.212.158.149:9200
ES_INDEX_PREFIX=q6          # 生产改为 swan

# LLM（OpenAI 兼容接口）
OPENAI_API_KEY=your_key
OPENAI_BASE_URL=your_base_url
LLM_MODEL=qwen3-max
```

### 连接工厂（config/db.py）

| 函数 | 库 | 用途 |
|------|----|------|
| `get_source_conn()` | `tian-gong`（只读） | Monitor 层 SQL 采集 |
| `get_monitor_conn()` | `tg_monitor`（读写） | 队列/历史/建议写入 |

---

## 5. 监控数据库表结构

### monitor_action_queue（核心队列表）

动作入队，等待人工审批（status=0→1/3，执行完成→2）。

### monitor_metric_history（指标历史）

每次采集后写入 value + level，90 天保留。阈值优化器的数据源。

### monitor_noise_rules（噪音规则审计）

日志分析器确认为噪音时写入，仅作审计，运行时过滤读 noise_rules.json。

### monitor_root_cause_reports（根因报告）

每日根因分析器写入一条 per-date 报告，`ON DUPLICATE KEY UPDATE` 幂等。

### scheduler_heartbeat（调度器心跳）

单行表（id=1 固定），`scheduler.py` 每分钟 UPSERT `last_beat_at`。`scripts/watchdog.py` 通过系统 cron 每 10 分钟检查，超期发 TG 告警。

### monitor_suggestions（建议审批表）

AI 分析器建议的 per-item 状态记录。

```sql
CREATE TABLE monitor_suggestions (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  source        VARCHAR(32)  NOT NULL,   -- threshold_optimizer / log_analyzer / root_cause_analyzer
  analysis_date DATE         NOT NULL,
  item_type     VARCHAR(32)  NOT NULL,   -- adjust / noise / new_rule / new_chain
  metric_key    VARCHAR(128) NULL,       -- 幂等键
  payload       JSON         NOT NULL,   -- 完整建议内容
  status        TINYINT      NOT NULL DEFAULT 0,  -- 0=待审批 1=已采纳 2=已拒绝
  reviewed_at   BIGINT       NULL,
  reviewed_by   VARCHAR(64)  NULL,
  created_at    BIGINT       NOT NULL,
  UNIQUE KEY uq_source_date_key (source, analysis_date, metric_key)
);
```

---

## 6. 建议审批系统

### 数据流

```
AI 分析器（非交互模式）
    ↓ insert_suggestions(rows)
monitor_suggestions（DB）
    ↓ GET /api/suggestions?status=0
suggestions_detail.html（浏览器）
    ↓ POST /api/suggestions/{id}/approve（含 overrides）
api/routes/suggestions.py
    ├── threshold_optimizer + adjust → api/actions/threshold.py → thresholds_{domain}.json
    ├── log_analyzer + noise → api/actions/noise.py → noise_rules.json + monitor_noise_rules
    ├── log_analyzer + new_rule → monitor_noise_rules（仅记录，待建规则）
    └── root_cause_analyzer + new_chain → api/actions/chain.py → causal_chains.json
```

### 拒绝抑制

分析器每次调用 LLM 前，从 DB 查询近期被拒绝的建议作为上下文：

| 来源 | 抑制窗口 |
|------|--------|
| threshold_optimizer | 30 天 |
| log_analyzer | 14 天 |
| root_cause_analyzer | 30 天 |

### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/suggestions` | 查询建议列表，支持 `?status=&source=` |
| POST | `/api/suggestions/{id}/approve` | 采纳，body 含可选 `overrides` |
| POST | `/api/suggestions/{id}/reject` | 拒绝 |

---

## 7. AI 分析器说明

### 阈值优化器（threshold_optimizer.py）

- **触发：** 每周日 02:00（非交互），或手动 `python -m engine.threshold_optimizer`
- **前提：** monitor_metric_history 中有 ≥ 7 天数据（≥ 100 条/指标）
- **流程：** load_all_stats（30 天回溯） → 域级 LLM × 6（并行）→ 跨域综合 LLM × 1 → save_cache → insert_suggestions
- **建议类型：** adjust（阈值调整）、keep（无需调整）、insufficient_data

### 日志智能分析器（log_analyzer.py）

- **触发：** 每天 04:00（非交互），或手动 `python -m engine.log_analyzer`
- **流程：** ES significant_terms 聚合（24h，背景基线 7d）→ 噪音预过滤 → LLM 分类 → save_cache → insert_suggestions
- **建议类型：** new_rule（新监控规则）、noise（确认为噪音）、watch（持续关注）
- **`--agg-only`：** 仅 ES 聚合，不调 LLM；`--from-cache`：复用昨日 LLM 结果并走 CLI

### 根因分析器（root_cause_analyzer.py）

- **触发：** 每天 05:00，或手动 `python -m engine.root_cause_analyzer`
- **前提：** 近 24h alerts.log 中有 ≥ 5 条告警
- **流程：** 读 monitor_metric_history + alerts.log → LLM 单次分析 → save_cache → _save_report_to_db → insert_suggestions（suggested_new_chains）
- **建议类型：** new_chain（建议新增因果链）

### 实时根因关联（correlation_engine.py）

每次告警后自动触发（在 alert_engine.handle 之后），best-effort：

1. 查 monitor_metric_history 近 30min 内其他域的 warning/critical 记录
2. 匹配 config/causal_chains.json → confidence="known"
3. 时序相近但未命中因果链 → confidence="suspected"
4. 结果写入 alerts.log 每条记录的 `correlation` 字段
5. DB 异常时降级为 none，不阻断告警

---

## 8. 调度器说明

**入口文件：** `scheduler.py`（注意：已改为 `MonitorDashboard(port=0)`，不内置 HTTP server）  
**运行方式：** `python scheduler.py`

### 调度表

| job id | 触发方式 | 频率/时间 |
|--------|----------|-----------|
| payment_recharge | interval | 5 分钟 |
| payment_channel_balance | interval | 15 分钟 |
| payment_withdraw_queue | interval | 15 分钟 |
| payment_withdraw_fail_rate | interval | 60 分钟 |
| game_balance_transfer | interval | 10 分钟 |
| game_reconciliation | cron | 每日 02:00 |
| risk_alert_backlog | interval | 10 分钟 |
| risk_alert_timeout | interval | 30 分钟 |
| risk_event_backlog | interval | 10 分钟 |
| risk_blacklist_expiry | interval | 60 分钟 |
| activity_redemption | interval | 15 分钟 |
| activity_first_deposit | interval | 5 分钟 |
| account_frozen_balance | interval | 60 分钟 |
| operation_vip_adjust | interval | 60 分钟 |
| operation_balance_adjustment | interval | 10 分钟 |
| operation_config_change | interval | 60 分钟 |
| log_* (5 条) | interval | 5-30 分钟 |
| scheduler_heartbeat | interval | 每分钟 |
| threshold_optimizer | cron | 每周日 02:00 |
| log_analyzer | cron | 每天 04:00 |
| root_cause_analyzer | cron | 每天 05:00 |
| metric_history_writer | interval | 每次采集后 |

---

## 9. 阈值配置体系

### METRIC_THRESHOLD_MAP（engine/threshold_config.py）

将 metric 名称映射到 `(domain, sub_key, {role: json_key})` 三元组，供阈值优化器统计层和 API action handler 使用。公共化后可跨模块 import，不需要依赖 threshold_optimizer.py。

### 配置文件热重载

- `thresholds_*.json`：scheduler job 每次执行时调 `load_thresholds(domain)`，下一个 check cycle 自动生效
- `noise_rules.json`：log_analyzer 每次运行时加载，每次分析生效
- `causal_chains.json`：correlation_engine 每条告警触发时读取，写文件后下一告警生效

---

## 10. 数据流全貌

```
Monitor 采集层（SQL/ES）
    ↓ list[MetricResult]
规则引擎（rule_engine.evaluate）
    ↓ list[RuleResult]（level / action）
        ├── alert_engine.handle  → logs/alerts.log
        │       └── correlation_engine  → alerts.log.correlation（best-effort）
        ├── dashboard.update     → output/monitor_dashboard.html（scheduler 写）
        └── action_executor.enqueue → monitor_action_queue（仅 action="enqueue"）

AI 分析器（离线，cron）
    ↓ insert_suggestions
monitor_suggestions（DB）
    ↓ Web 审批（http://localhost:8080/suggestions_detail.html）
    └── approve → action handler → thresholds_*.json / noise_rules.json / causal_chains.json
```

---

## 11. 关键设计决策

### 为什么规则引擎主导，而非 AI 实时判断？

实时监控需要毫秒级确定性响应，AI 推理有延迟（通常 1–5 秒/次）且成本高。规则引擎对 19 条业务规则做硬编码阈值判断，延迟 < 1ms，可审计。AI 的角色是**离线优化者**：分析历史数据，建议更新配置，人工 Web 审批确认后写回——不参与实时决策路径。

### 为什么建议系统改为 FastAPI 独立进程？

- 职责分离：API 挂了不影响调度，调度挂了不影响 Web 审批
- FastAPI 在 daemon 线程里运行有已知限制（信号、reload），独立进程更稳
- DB 是两个进程唯一的共享状态，无需进程间通信

### 读写分库的必要性

`tian-gong` 是 300+ 张表的生产业务库，任何 INSERT/UPDATE 都可能触发行锁。监控系统读操作用只读账号，写动作落到独立的 `tg_monitor` 库，从源头杜绝监控影响业务。

---

## 12. 常用命令速查

```bash
# 启动调度器
python scheduler.py

# 启动 API server（端口 8080）
python -m api.server

# 手动触发阈值优化（完整分析，建议写 DB）
python -m engine.threshold_optimizer

# 手动触发日志分析（完整分析，建议写 DB）
python -m engine.log_analyzer

# 手动触发根因分析
python -m engine.root_cause_analyzer

# 运行测试（当前 144 项）
python -m pytest tests/ -v

# 查看最新告警
tail -n 50 logs/alerts.log

# 查看待审批建议（DB）
# SELECT * FROM monitor_suggestions WHERE status=0 ORDER BY created_at DESC;

# 查看待审批动作（DB）
# SELECT id, domain, action_type, triggered_by FROM monitor_action_queue WHERE status=0;
```

---

## 附录：核心数据结构

```python
@dataclass
class MetricResult:
    domain: str
    metric: str
    value: float
    channel_id: int | None = None
    extra: dict = field(default_factory=dict)

@dataclass
class RuleResult:
    level: Literal["ok", "warning", "critical"]
    action: Literal["none", "alert", "enqueue"]
    metric: MetricResult
    threshold: float
    message: str
```
