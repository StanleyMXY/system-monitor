# AI 离线阈值优化器设计文档（Phase 4）

**日期：** 2026-05-03
**状态：** 已批准，待实现

---

## 背景

Phase 1–3 + 日志监控已完成 21 个指标的实时采集与告警。目前所有阈值均为人工拍定的初始值，缺乏数据支撑的复盘机制。运行一段时间后，部分阈值可能过于宽松（正常波动从不触发）或过于敏感（频繁误报），需要一个基于历史数据的优化工具。

阈值优化器是一个离线分析工具，定期读取历史指标数据，通过统计层 + LLM 两层分析，生成带理由的阈值调整建议，由人工交互式确认后写入配置文件。

---

## 设计原则

- **统计层确定性**：p50/p95/p99 等数字由 Python 精确计算，LLM 只负责解读不负责算数
- **LLM 层叠推理**：域级分析先跑，跨域综合基于域级结论做注解，不独立产生结论，保证逻辑一致性
- **对主流程零侵入**：历史写入失败只打 warning log，不影响告警主路径
- **与运营分析系统统一**：复用相同结构的 `utils/llm.py`，共用同一份 `.env` LLM 配置

---

## 1. 整体数据流

```
scheduler（持续运行）
  └─► MetricResult list → INSERT INTO monitor_metric_history

threshold_optimizer（每周日 02:00 自动 / 随时手动）
  │
  ├─ 1. 统计层（确定性，无 LLM）
  │     读 monitor_metric_history（过去 30 天）
  │     → 每个指标计算 p50/p95/p99、告警频率、触发率
  │
  ├─ 2. 域级分析（LLM × 6，并行）
  │     输入：当前阈值 JSON + 该域所有指标统计摘要
  │     输出：每域一份 JSON 报告（含 per-metric 建议 + 理由）
  │
  ├─ 3. 跨域综合（LLM × 1）
  │     输入：6 份域级报告 + 跨域告警时序重叠数据
  │     输出：对已有建议的注解（不推翻，只补充）
  │
  └─ 4. 交互式 CLI
        逐条展示建议 + 理由 + 跨域备注
        [a] 采纳 → 改 JSON   [s] 跳过   [e] 编辑数值   [q] 保存退出
```

---

## 2. 数据库表

### monitor_metric_history（新增，写入 tg_monitor）

```sql
CREATE TABLE IF NOT EXISTS monitor_metric_history (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  domain      VARCHAR(32)   NOT NULL,
  metric      VARCHAR(64)   NOT NULL,
  channel_id  INT           NULL,
  value       DECIMAL(18,4) NOT NULL,
  level       VARCHAR(16)   NOT NULL,
  recorded_at BIGINT        NOT NULL,
  INDEX idx_metric_time (metric, recorded_at),
  INDEX idx_domain_time (domain, recorded_at)
);
```

- `level`：`ok` / `warning` / `critical`，来自 RuleResult
- `recorded_at`：毫秒时间戳，与现有表保持一致

**保留策略**：调度器每天 03:00 执行一次清理，删除 90 天前的数据，控制表体积。

### 历史写入（修改 scheduler.py）

在 `_run_monitor()` 和 `_run_log_monitor()` 的 `evaluate()` 调用之后，新增：

```python
try:
    _write_metric_history(rule_results)
except Exception as exc:
    logger.warning(f"历史指标写入失败（非致命）: {exc}")
```

`_write_metric_history(rule_results)` 批量 INSERT，从 `RuleResult.metric`（MetricResult）和 `RuleResult.level` 取值。对现有告警主路径零侵入。

---

## 3. 统计层

优化器需要 ≥ 7 天数据才运行，否则退出并提示。

每个指标产出以下统计摘要：

```python
{
  "metric": "recharge_success_rate",
  "domain": "payment",
  "sample_count": 8640,
  "p50": 0.921,
  "p95": 0.871,
  "p99": 0.832,
  "min": 0.61,
  "max": 0.98,
  "alert_rate_7d": 0.0003,
  "alert_rate_30d": 0.0003,
  "alert_count_30d": 3,
  "current_warning": 0.80,
  "current_critical": 0.60,
  "gap_p95_vs_warning": +0.071   # 正值 = 阈值偏宽松，负值 = 阈值偏严
}
```

---

## 4. LLM 接口

### utils/llm.py（新建，与 TG-Ads-Analysis 同结构）

```python
from langchain.chat_models.base import init_chat_model
from dotenv import load_dotenv

load_dotenv()

def get_llm(temperature=0.3):
    return init_chat_model(
        model="qwen3-max",
        model_provider="openai",
        temperature=temperature,
    )
```

温度设为 0.3（低于运营分析系统的 0.8），优化器需要稳定一致的分析结论。

### 域级分析（LLM × 6）

**输入（每域）：**
- 当前域阈值 JSON
- 该域所有指标的统计摘要列表
- 业务域说明（写在 system prompt 里，如"支付域负责充值/提现，critical 级别影响资金安全"）

**输出 JSON schema（system prompt 里约束）：**

```json
{
  "domain": "payment",
  "recommendations": [
    {
      "metric": "recharge_success_rate",
      "action": "adjust",
      "current": {"warning": 0.80, "critical": 0.60},
      "suggested": {"warning": 0.87, "critical": 0.68},
      "reason": "过去30天p95=0.871，当前阈值触发率仅0.03%，阈值过于宽松"
    }
  ]
}
```

`action` 枚举：
- `adjust`：有明确建议，进入 CLI 确认流程
- `keep`：当前阈值合理，不弹出确认
- `insufficient_data`：数据不足（< 7 天 / 样本 < 100），跳过

### 跨域综合（LLM × 1）

**输入：**
- 6 份域级报告（含 recommendations）
- 跨域告警时序重叠表：近 30 天内同一天有多个域同时触发告警的记录

**输出：**

```json
{
  "cross_domain_notes": [
    {
      "metric": "recharge_success_rate",
      "note": "上周三与风控域高危事件积压同日触发，可能同源，建议观察后再决定是否调整阈值"
    }
  ]
}
```

注解通过 `metric` 字段挂载到对应建议，在 CLI 中展示，不阻塞采纳流程，不修改 `action` 字段。

---

## 5. 交互式 CLI

```
============================================================
建议 #3 / 11   [payment] recharge_success_rate
------------------------------------------------------------
当前阈值   warning=0.80   critical=0.60
建议阈值   warning=0.87   critical=0.68
理  由   过去30天p95=0.871，当前阈值触发率仅0.03%（3次/8640次），
         阈值过于宽松，建议上调至接近p95水位以提升灵敏度
⚠ 跨域   上周三与风控域高危事件同日触发，可能同源，建议观察后再决定
------------------------------------------------------------
统计速览  p50=0.921  p95=0.871  p99=0.832  30天告警3次
------------------------------------------------------------
[a] 采纳   [s] 跳过   [e] 编辑数值   [d] 完整统计   [q] 保存退出
>
```

- `[a]`：立即写入对应 `thresholds_*.json`
- `[e]`：人工输入数值覆盖 LLM 建议，再写入
- `[q]`：已采纳的保存，未处理的跳过，打印 summary
- `action=keep` 的建议不进入 CLI，在报告末尾统一列出"无需调整"列表

**结束 summary：**
```
已采纳 4 条 / 编辑 2 条 / 跳过 5 条
已更新：thresholds_payment.json, thresholds_risk.json
```

### 运行方式

```bash
# 完整分析（统计 + LLM + CLI）
python -m engine.threshold_optimizer

# 使用上次缓存的 LLM 输出，直接进入 CLI
python -m engine.threshold_optimizer --from-cache

# 仅统计层，打印摘要，不调 LLM（调试用）
python -m engine.threshold_optimizer --stats-only
```

---

## 6. 缓存机制

LLM 分析结果写入 `output/optimizer_cache_YYYYMMDD.json`，`--from-cache` 时读取最新文件。避免重复调用 LLM（每次 7 个调用，有成本）。

---

## 7. 调度

在 `scheduler.py` 新增：

```python
scheduler.add_job(job_run_threshold_optimizer, "cron",
                  day_of_week="sun", hour=2, minute=0,
                  id="threshold_optimizer", max_instances=1)
```

调度触发时以非交互模式运行（只做统计 + LLM，结果写入缓存和日志），不弹 CLI。人工随时 `--from-cache` 查看并确认。

---

## 8. 文件清单

| 操作 | 文件 | 说明 |
|---|---|---|
| 新建 | `db/migrate_phase4.sql` | monitor_metric_history 迁移脚本 |
| 新建 | `utils/__init__.py` | |
| 新建 | `utils/llm.py` | LangChain init_chat_model，同 TG-Ads-Analysis |
| 新建 | `engine/threshold_optimizer.py` | 统计层 + LLM 调用 + CLI |
| 新建 | `tests/test_threshold_optimizer.py` | 统计函数单元测试 |
| 修改 | `db/init_monitor.sql` | 补充 monitor_metric_history 表定义 |
| 修改 | `scheduler.py` | 写历史表 + 注册每周优化器 job + 每日数据清理 |
| 修改 | `.env.example` | 补充 LLM 相关环境变量（OPENAI_API_KEY、OPENAI_BASE_URL、model） |

---

## 9. 依赖

```
langchain          # LLM 调用
langchain-openai   # OpenAI-compatible provider
python-dotenv      # 已有
```
