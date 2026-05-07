# 建议审批系统重构设计

**日期**：2026-05-08  
**状态**：待实施

## 背景

当前三个分析器（threshold_optimizer / log_analyzer / root_cause_analyzer）的建议均写入 JSON 缓存文件，无 per-item 审批状态，CLI 只能全批采纳，且缓存被下次分析覆盖后建议丢失。目标是改为 Web 页面逐条审批，已拒绝的建议不在下次分析中重复出现。

## 整体架构

采用独立进程方案：

- `scheduler.py`：不变，继续运行调度 + `MonitorDashboard` 写 HTML 文件到 `output/`
- `api/server.py`：新增 FastAPI 应用，端口 8080，替代原 `SimpleHTTPRequestHandler`，负责 serve 静态文件 + 全部建议 REST API

启动方式：
```
python scheduler.py      # 调度器（不再启动 HTTP server）
python -m api.server     # API + 静态文件服务器（端口 8080）
```

`scheduler.py` 唯一改动：`MonitorDashboard(port=8080)` → `MonitorDashboard(port=0)`，禁用内嵌 HTTP server（已有该分支，`start()` 直接 return）。

DB 是两个进程之间的唯一共享状态。JSON 缓存文件保留，仅用于 `--from-cache` 调试，不再是 Web 数据源。

---

## §1 数据层

### 新表 `monitor_suggestions`

```sql
CREATE TABLE monitor_suggestions (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  source        VARCHAR(32)  NOT NULL,
  analysis_date DATE         NOT NULL,
  item_type     VARCHAR(32)  NOT NULL,
  metric_key    VARCHAR(128) NULL,
  payload       JSON         NOT NULL,
  status        TINYINT      NOT NULL DEFAULT 0,
  reviewed_at   BIGINT       NULL,
  reviewed_by   VARCHAR(64)  NULL,
  created_at    BIGINT       NOT NULL,
  UNIQUE KEY uq_source_date_key (source, analysis_date, metric_key)
);
```

字段说明：

| 字段 | 说明 |
|------|------|
| `source` | `threshold_optimizer` / `log_analyzer` / `root_cause_analyzer` |
| `item_type` | `adjust` / `noise` / `new_rule` / `new_chain` |
| `metric_key` | 幂等键，见各分析器说明 |
| `payload` | 完整建议内容（JSON），Web 展示和执行采纳均从此字段取 |
| `status` | 0=待审批 1=已采纳 2=已拒绝 |
| `reviewed_at` | 毫秒时间戳 |

UNIQUE 约束 `(source, analysis_date, metric_key)` 保证同天重跑幂等。

### 迁移脚本

新增 `db/migrate_suggestions.sql`。

---

## §2 API Server

### 模块结构

```
api/
  __init__.py
  server.py       # FastAPI app 入口，uvicorn 启动
  routes/
    suggestions.py  # /api/suggestions 路由
  actions/
    threshold.py    # 采纳阈值建议：写 thresholds_*.json
    noise.py        # 采纳噪音规则：写 noise_rules.json + DB
    chain.py        # 采纳因果链：写 causal_chains.json
```

### 路由

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 重定向到 `monitor_dashboard.html` |
| GET | `/{filename}` | serve `output/` 目录静态文件 |
| GET | `/api/suggestions` | 查询建议列表，支持 `?status=&source=` 过滤 |
| POST | `/api/suggestions/{id}/approve` | 采纳，body 可含 `overrides` 字段覆盖 payload |
| POST | `/api/suggestions/{id}/reject` | 拒绝 |

### approve 请求体

```json
{
  "overrides": {
    "suggested": { "warning": 0.85, "critical": 0.65 }
  },
  "reviewed_by": "operator"
}
```

`overrides` 可选，缺省时直接用 payload 中的原始建议值。

### 采纳动作映射

| source | item_type | 执行动作 |
|--------|-----------|----------|
| `threshold_optimizer` | `adjust` | 更新 `thresholds_{domain}.json` |
| `log_analyzer` | `noise` | 追加 `noise_rules.json` + INSERT `monitor_noise_rules` |
| `log_analyzer` | `new_rule` | INSERT `monitor_noise_rules`（待人工建规则） |
| `root_cause_analyzer` | `new_chain` | 追加 `causal_chains.json` |

### 热重载

- `thresholds_*.json`：scheduler job 每次运行调 `load_thresholds()`，写文件后下一 check cycle 自动生效
- `noise_rules.json`：log_analyzer 每日运行时加载，每次分析生效
- `causal_chains.json`：correlation_engine 每条告警触发时读取，写文件后下一告警生效

无需额外热重载机制。

---

## §3 分析器改动

### `_METRIC_THRESHOLD_MAP` 迁移

`threshold_optimizer.py` 中的 `_METRIC_THRESHOLD_MAP`（metric → domain/json文件映射）迁移到 `engine/threshold_config.py`，使 `api/actions/threshold.py` 可直接导入，避免 API server 依赖 analyzer 模块。

### 共用函数

新增 `engine/suggestions_store.py`，提供：

```python
def insert_suggestions(rows: list[dict]) -> None:
    """批量 INSERT IGNORE INTO monitor_suggestions。"""

def load_rejected_context(source: str, window_days: int) -> list[dict]:
    """查询近 window_days 内被拒绝的建议，用于 LLM 上下文。"""
```

### threshold_optimizer

- LLM 分析前：调 `load_rejected_context("threshold_optimizer", 30)` 拼入 system prompt
- `save_cache()` 后：调 `insert_suggestions()`，每条 `action=adjust` 的 recommendation INSERT 一行
  - `metric_key = rec["metric"]`
  - `payload = rec`

### log_analyzer

- LLM 分析前：调 `load_rejected_context("log_analyzer", 14)` 拼入 system prompt
- `save_cache()` 后：调 `insert_suggestions()`，`type in (new_rule, noise)` 的 pattern INSERT 一行
  - `metric_key = pattern["term"][:128]`
  - `payload = {**pattern, "samples": samples_by_term.get(term, [])}`

### root_cause_analyzer

- LLM 分析前：调 `load_rejected_context("root_cause_analyzer", 30)` 拼入 system prompt
- `_save_report_to_db()` 后：调 `insert_suggestions()`，每条 `suggested_new_chains` INSERT 一行
  - `metric_key = "{cause_domain}.{cause_metric}→{effect_domain}.{effect_metric}"`
  - `payload = chain`

### 非交互模式 CLI 行为

非交互模式下，分析器写完 DB 后直接 return，不再打印"待人工采纳"日志（建议已在 DB，Web 审批）。交互模式（手动运行）保留原 CLI 流程不变。

---

## §4 前端审批页

### 数据加载

`suggestions_detail.html` 打开时执行：

```js
fetch('/api/suggestions?status=0')
```

结果按 source 分三组渲染：阈值调整 / 因果链 / 日志模式。

### 各类型交互

**阈值调整（adjust）**
- 展示当前值 vs 建议值
- `warning` / `critical` 各有 `<input type="number">` 预填建议值，可手动改
- 「采纳」：把当前输入框值作为 `overrides.suggested` POST `/approve`
- 「拒绝」：POST `/reject`

**日志模式（noise / new_rule）**
- 展示 term、描述、根因、样本（最多 3 条）
- `suggested_phrases` 可编辑（textarea 预填）
- 「确认为噪音」/ 「记录为待建规则」：把编辑后的 phrases 作为 `overrides.suggested_phrases` POST `/approve`
- 「拒绝」：POST `/reject`

**因果链（new_chain）**
- 展示 cause→effect、description、confidence
- 纯二选一，无编辑字段

### 操作后行为

approve / reject 成功后：
- 该条目从列表 DOM 中移除（不刷页面）
- 顶部待审批 badge 数字 -1

### MonitorDashboard 建议计数

`_load_pending_suggestions()` 改为从 `monitor_suggestions` 表查 `status=0` 的计数，不再读 JSON 文件。`MonitorDashboard` 在 scheduler 进程内直接查 DB。分三组计数（threshold / chain / log）填回 result dict，其余字段结构不变。

---

## 拒绝抑制窗口

| source | 窗口 |
|--------|------|
| `threshold_optimizer` | 30 天 |
| `log_analyzer` | 14 天 |
| `root_cause_analyzer` | 30 天 |

LLM prompt 中的拒绝上下文格式（示例）：

```
以下建议已被人工拒绝（请勿重复建议）：
- [2026-05-01] metric=payment.recharge_rate，拒绝原因：无（人工拒绝）
```

---

## 不在本次范围内

- CLI 交互模式改动（保留现有逻辑）
- `monitor_noise_rules` 表结构变更
- 分析器调度时间调整
- 前端实时推送（SSE）
