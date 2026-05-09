# 天工平台 系统运营监控

天工游戏平台基础设施实时监控系统，覆盖支付、游戏供应商、风控、活动、账户、系统操作、日志 7 个域，共 21 个调度任务，集成 AI 阈值优化、日志智能分析、根因分析，以及 Web 建议审批系统。

---

## 功能概览

### 实时监控（7 个域）

| 域 | 主要指标 | 告警频率 |
|---|---|---|
| 支付域 | 充值成功率、渠道余额、提现积压、提现失败率 | 5-15 分钟 |
| 游戏供应商域 | 转账失败率、重试异常、厂商对账差异 | 15 分钟 / 每日 |
| 风控域 | 高危预警积压、超时未处理、黑名单到期 | 15-30 分钟 |
| 活动域 | 兑换失败率、首充异常 | 30 分钟 |
| 用户账户域 | 冻结余额增长率 | 30 分钟 |
| 系统操作域 | VIP 批量调整、大额余额调整、配置变更频率 | 15-30 分钟 |
| 日志域 | 游戏启动失败、MQ 路由故障、分表路由失败、WebSocket 异常、DB 唯一键冲突 | 5-30 分钟 |

### AI 智能优化（Phase 4/5）

- **阈值优化器**：每周分析历史指标数据，统计层计算 p50/p95/p99，LLM 域级分析 × 6 + 跨域综合 × 1，建议写入 Web 审批
- **日志智能分析器**：每日 ES significant_terms 聚合发现新错误模式，LLM 分类（new_rule / noise / watch），建议写入 Web 审批
- **根因分析器**：每日分析近 24h 告警序列，LLM 识别和建议因果链，建议写入 Web 审批
- **实时根因关联**：每次告警触发时，自动关联近 30min 跨域告警，匹配已知因果链

### Web 建议审批（重构后）

所有 AI 分析器的建议统一写入 `monitor_suggestions` 表，通过 `http://localhost:8080/suggestions_detail.html` 逐条审批：

- 阈值调整：可直接编辑数值后采纳
- 日志模式：可编辑过滤词后确认为噪音或记录为待建规则
- 因果链：二选一采纳/拒绝

已拒绝的建议在下次分析时作为 LLM 上下文，抑制重复建议（阈值/链 30 天，日志模式 14 天）。

---

## 架构

```
scheduler.py（APScheduler AsyncIOScheduler）
    ├── monitor/          采集层（只读 DB 查询 + ES 查询）
    ├── engine/
    │   ├── rule_engine.py          阈值规则评估
    │   ├── alert_engine.py         告警日志
    │   ├── correlation_engine.py   实时根因关联
    │   ├── threshold_optimizer.py  AI 阈值优化器
    │   ├── log_analyzer.py         AI 日志智能分析器
    │   ├── root_cause_analyzer.py  AI 根因分析器
    │   └── suggestions_store.py    建议 DB 读写
    ├── executor/         动作入队（写 tg_monitor.monitor_action_queue）
    ├── dashboard/        看板 HTML 生成（不含 HTTP server）
    └── config/           阈值 JSON + DB/ES 连接配置

api/server.py（FastAPI，端口 8080）
    ├── 静态文件：serve output/ 目录
    └── REST API：GET/POST /api/suggestions/{id}/approve|reject
```

**读写分离：**
- `SOURCE_*` → tian-gong 生产库（只读）
- `MONITOR_*` → tg_monitor 监控库（读写）

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 填写 SOURCE_*/MONITOR_* 数据库连接信息
# 填写 ES_URL、OPENAI_API_KEY、OPENAI_BASE_URL（AI 功能需要）
# 填写 TG_BOT_TOKEN、TG_ALERT_CHAT_IDS，并将 TG_NOTIFY_ENABLED 改为 true（TG 告警需要）
```

### 3. 初始化监控库

```bash
mysql -u <MONITOR_USER> -p tg_monitor < db/init_monitor.sql
mysql -u <MONITOR_USER> -p tg_monitor < db/migrate_phase4.sql
mysql -u <MONITOR_USER> -p tg_monitor < db/migrate_log_analyzer.sql
mysql -u <MONITOR_USER> -p tg_monitor < db/migrate_phase5.sql
mysql -u <MONITOR_USER> -p tg_monitor < db/migrate_suggestions.sql
mysql -u <MONITOR_USER> -p tg_monitor < db/migrate_heartbeat.sql
```

### 4. 启动服务

```bash
# 调度器（常驻，负责采集、告警、写 HTML）
python scheduler.py

# API server（负责 HTTP 服务和审批 API，端口 8080）
python -m api.server
```

看板地址：http://localhost:8080/monitor_dashboard.html  
审批页面：http://localhost:8080/suggestions_detail.html

### 5. 配置 Watchdog（可选）

通过系统 cron 每 10 分钟检查调度器心跳，超过 10 分钟无心跳则发送 TG 告警：

```bash
*/10 * * * * cd /path/to/project && python -m scripts.watchdog >> logs/watchdog.log 2>&1
```

---

## AI 功能使用

### 阈值优化器

需要调度器运行至少 7 天积累历史数据后可用。

```bash
# 完整分析（统计 + LLM）；建议自动写入 DB，通过 Web 审批
python -m engine.threshold_optimizer

# 仅查看统计数据，不调用 LLM
python -m engine.threshold_optimizer --stats-only

# 使用上次缓存的 LLM 结果，通过 CLI 交互审批（debug 用）
python -m engine.threshold_optimizer --from-cache
```

调度器每周日 02:00 自动在非交互模式运行，建议写入 DB 后通过 Web 审批。

### 日志智能分析器

```bash
# 完整分析（ES 聚合 + LLM）；建议自动写入 DB，通过 Web 审批
python -m engine.log_analyzer

# 仅查看 ES 聚合结果，不调用 LLM
python -m engine.log_analyzer --agg-only

# 使用上次缓存的 LLM 结果，通过 CLI 交互审批（debug 用）
python -m engine.log_analyzer --from-cache
```

调度器每天 04:00 自动在非交互模式运行。

### 根因分析器

```bash
# 完整分析；建议新因果链写入 DB，通过 Web 审批
python -m engine.root_cause_analyzer

# 使用上次缓存的 LLM 结果
python -m engine.root_cause_analyzer --from-cache
```

调度器每天 05:00 自动运行。

---

## tg_monitor 数据库表

| 表 | 用途 |
|---|---|
| `monitor_action_queue` | 高危动作入队，等待人工审批 |
| `monitor_metric_history` | 历史指标时序数据（阈值优化器数据源，90 天保留） |
| `monitor_noise_rules` | 已确认的噪音规则审计记录 |
| `monitor_root_cause_reports` | 每日根因分析报告 |
| `monitor_suggestions` | AI 分析器建议（per-item 审批状态） |
| `scheduler_heartbeat` | 调度器心跳（单行），watchdog 检查存活用 |

---

## 告警分级

| 级别 | 含义 | 动作 |
|---|---|---|
| `critical` | 严重影响业务 | 写入 `monitor_action_queue` + 告警日志 + TG 推送（含根因） |
| `warning` | 需要关注 | 告警日志 + dashboard |
| `ok` | 正常 | 无 |

---

## 测试

```bash
python -m pytest tests/ -v
```

当前通过 158 个测试用例。

---

## 噪音过滤

已知的系统噪音（如 RabbitMQ 插件缺失导致的持续报错）可在 `config/noise_rules.json` 中配置，系统在 ES 查询层通过 `must_not` 子句过滤，不计入错误次数，也不触发告警。

通过日志智能分析器确认的新噪音会自动写入此文件。
