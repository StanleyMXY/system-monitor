# Phase 5 设计：根因关联分析

**日期**：2026-05-05  
**状态**：已确认，待实施

---

## 目标

将系统从"监控自动化"升级到"运营辅助诊断"：告警触发时自动附带根因提示，同时每日定时产出因果链深度报告，让运营人员从"知道发生了什么"升级到"知道为什么"。

**不在本 Phase 范围：**
- Telegram 通知（独立 Phase）
- 自动执行动作（无业务接口，暂不可行）

---

## 架构

### 数据流

```
现有链路（不变）：
  monitor → rule_engine → alert_engine.handle() → alerts.log

Phase 5 新增（实时层）：
  alert_engine.handle()
    └─→ correlation_engine.correlate(result)
          ├─ 内部开 get_monitor_conn()（同 action_executor 模式）
          ├─ 查 monitor_metric_history（近 30min 跨域告警）
          ├─ 匹配 config/causal_chains.json
          └─ 返回 CorrelationResult
    └─→ alerts.log（原 JSON 加 correlation 字段）

Phase 5 新增（定时层）：
  scheduler → job_run_root_cause_analyzer（每日 05:00）
    └─→ engine/root_cause_analyzer.py
          ├─ 读 monitor_metric_history（近 24h）
          ├─ 读 alerts.log（近 24h）
          ├─ LLM 分析因果链序列
          └─→ output/root_cause_YYYYMMDD.json
              monitor_root_cause_reports 表
```

### 新增 / 改动文件

| 文件 | 类型 | 说明 |
|---|---|---|
| `engine/correlation_engine.py` | 新增 | 实时关联逻辑 |
| `config/causal_chains.json` | 新增 | 预定义因果链配置 |
| `engine/root_cause_analyzer.py` | 新增 | 深度 LLM 分析器 |
| `db/migrate_phase5.sql` | 新增 | `monitor_root_cause_reports` 表 |
| `engine/alert_engine.py` | 改动 | `handle()` 调用 correlation_engine |
| `scheduler.py` | 改动 | 注册 05:00 cron job |

---

## 实时关联引擎（correlation_engine.py）

### 数据结构

```python
@dataclass
class CorrelationResult:
    confidence: Literal["known", "suspected", "none"]
    cause_domain: str | None      # 根因所在域
    cause_metric: str | None      # 根因指标
    cause_ts: int | None          # 根因告警时间戳（毫秒）
    lead_minutes: float | None    # 根因比当前告警早多少分钟
    message: str                  # 人读文本，"none" 时为空字符串

def correlate(result: RuleResult) -> CorrelationResult:
    # 内部自行开 get_monitor_conn()，用完关闭；不接受外部 conn 参数
    ...
```

### 执行逻辑

1. `result.level == "ok"` → 直接返回 `confidence="none"`，不查 DB
2. 查 `monitor_metric_history`：`level != 'ok'` AND `domain != 当前域` AND `recorded_at ∈ [now-30min, now-1min]`
3. **优先匹配已知因果链**（causal_chains.json）：找到上游指标 → `confidence="known"`
4. **降级时序接近**：无已知链但有跨域告警 → 取时间最近一条，`confidence="suspected"`
5. 两者都没有 → `confidence="none"`，不附提示

### alerts.log entry 示例

```json
{
  "ts": 1746400000,
  "level": "critical",
  "domain": "payment",
  "metric": "recharge_success_rate",
  "value": 0.61,
  "threshold": 0.70,
  "message": "充值成功率严重低于阈值: 61.0% < 70.0%",
  "correlation": {
    "confidence": "known",
    "cause_domain": "log",
    "cause_metric": "mq_route_error_count",
    "cause_ts": 1746399628000,
    "lead_minutes": 6.2,
    "message": "已知因果：MQ 路由故障于 6.2 分钟前触发，本次告警大概率为其下游影响"
  }
}
```

`confidence="suspected"` 时 message 格式：  
`"时序相关（待核查）：log 域 mq_route_error_count 于 6.2 分钟前有告警，可能关联"`

`confidence="none"` 时不写 `correlation` 字段。

---

## 因果链配置（config/causal_chains.json）

初始内置 6 条已知因果链，格式：

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

深度分析器可建议新增条目，人工 `[a]` 采纳后追加到此文件。

---

## 深度分析器（root_cause_analyzer.py）

### 执行流程

1. **加载数据**：`monitor_metric_history` 近 24h warning/critical 记录 + `alerts.log` 近 24h 条目（含 `correlation` 字段）
2. **预处理**：按时间升序排列，展平为告警事件列表；< 5 条直接跳过退出
3. **LLM 分析**（单次调用）：
   - 输入：告警序列 + 已有实时关联结果 + 当前 `causal_chains.json`
   - 输出：识别出的因果链（多条）、每条链的置信度和证据、与已知模式的吻合/新发现、建议新增的因果链规则（格式化，可直接写入 config）
4. **写文件**：`output/root_cause_YYYYMMDD.json`（原始数据 + LLM 结果）
5. **写 DB**：`monitor_root_cause_reports` 表（analysis_date + report_json + suggested_chains）
6. **interactive 模式**：控制台打印摘要，提供 `[a]` 采纳建议链写入 causal_chains.json / `[q]` 退出

### CLI

```
python -m engine.root_cause_analyzer           # 交互模式（含 [a] 采纳）
python -m engine.root_cause_analyzer --from-cache  # 跳过 LLM，复用当日缓存
```

调度器以 `interactive=False` 运行：LLM 分析 + 写入，不执行采纳。

---

## DB Schema

```sql
CREATE TABLE monitor_root_cause_reports (
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

迁移文件：`db/migrate_phase5.sql`

---

## 调度器变更

`scheduler.py` 新增：

```python
async def job_run_root_cause_analyzer():
    # 每日 05:00，非交互模式
    ...

scheduler.add_job(job_run_root_cause_analyzer, "cron",
                  hour=5, minute=0,
                  id="root_cause_analyzer", max_instances=1)
```

---

## 测试要点

| 场景 | 预期 |
|---|---|
| 告警触发，有已知因果链匹配 | correlation.confidence = "known"，message 含根因描述 |
| 告警触发，无已知链但有跨域时序邻近 | confidence = "suspected"，message 含"待核查"标注 |
| 告警触发，近 30min 无其他域告警 | 不写 correlation 字段 |
| level = "ok" | 不查 DB，直接跳过 |
| root_cause_analyzer 告警数 < 5 | 跳过 LLM，正常退出 |
| root_cause_analyzer --from-cache | 不调 LLM，复用缓存文件 |
| suggested_chains 采纳后 | causal_chains.json 新增对应条目 |
