# 日志智能分析器设计文档（Phase 4）

**日期：** 2026-05-03
**状态：** 已批准，待实现

---

## 背景

现有日志监控（Phase 3 补充）通过 5 条固定规则匹配已知错误关键词，无法发现未预设的新错误模式，也无法自动识别反复触发的已知噪音。

日志智能分析器是一个离线分析工具，每天运行一次，通过 ES significant_terms 聚合发现近 24 小时内异常突出的日志模式，由 LLM 分类分析，经人工确认后：
- 新规则候选沉淀为待实现的监控规则
- 确认噪音写入噪音规则库，在 ES 查询层直接过滤，减少现有规则的误报

---

## 设计原则

- **ES 做发现，LLM 做解读**：significant_terms 是统计性发现，LLM 补充业务语义，各司其职
- **噪音规则运行时生效**：`noise_rules.json` 在 `_count_errors` 查询层加 `must_not`，不需要改规则逻辑
- **不需要记忆系统**：监控系统的"记忆"是数据库和配置文件，不需要 LLM 知识库层
- **对运营系统零影响**：只对 ES 做只读查询，ES 与业务数据库完全隔离

---

## 1. 整体数据流

```
每天 04:00（或手动触发）
  │
  ├─ 1. ES 聚合层
  │     查询所有 {prefix}* 索引近 24h WARN/ERROR/Exception 日志
  │     significant_terms 聚合 message 字段
  │     背景基线 = 过去 7 天同类日志
  │     → Top 20 候选模式（词条 + 频次 + 显著性分数）
  │     → 每个候选拉取 3 条最新样本日志
  │     → 与 noise_rules.json 比对，已知噪音提前过滤
  │
  ├─ 2. LLM 分析层（单次调用）
  │     输入：候选模式 + 样本 + 现有监控规则摘要 + 已知噪音规则
  │     高频新模式 → 详细分析（根因/影响/建议关键词）
  │     低频/相似已有规则 → 精简判断
  │     输出：每个模式的分类（new_rule / noise / watch）+ 建议
  │
  └─ 3. 交互式 CLI
        new_rule：[a] 记录为待建规则（写 DB）  [s] 跳过
        noise：  [n] 确认为噪音（写 JSON + DB）[s] 跳过
        watch：  仅展示，自动进入下一条
        结束打印 summary
```

---

## 2. ES 聚合层

### 查询范围

- 索引：`{prefix}*`（覆盖 app/mw/playgame/supplier 等全部服务）
- `prefix` 从环境变量 `ES_INDEX_PREFIX` 读取（测试：`q6`，生产：`swan`）

### 第一步：significant_terms 聚合

```python
{
    "query": {
        "bool": {
            "must": [
                {"range": {"@timestamp": {"gte": "now-24h"}}},
                {"bool": {"should": [
                    {"match": {"message": "WARN"}},
                    {"match": {"message": "ERROR"}},
                    {"match": {"message": "Exception"}},
                ]}}
            ]
        }
    },
    "aggs": {
        "new_patterns": {
            "significant_terms": {
                "field": "message",
                "size": 20,
                "background_filter": {
                    "range": {"@timestamp": {"gte": "now-7d"}}
                },
                "min_doc_count": 5
            }
        }
    },
    "size": 0
}
```

返回 Top 20 候选，每条包含：`key`（词条）、`doc_count`（24h 频次）、`score`（显著性分数）。

### 第二步：拉取样本日志

对每个候选词条，查询 3 条最新日志：

```python
{
    "query": {"bool": {"must": [
        {"match": {"message": term}},
        {"range": {"@timestamp": {"gte": "now-24h"}}}
    ]}},
    "size": 3,
    "sort": [{"@timestamp": {"order": "desc"}}],
    "_source": ["message", "@timestamp"]
}
```

### 噪音预过滤

加载 `config/noise_rules.json`，对每个候选检查是否与任意噪音规则的 `must_phrases` 完全匹配。匹配则跳过，不进入 LLM。

---

## 3. LLM 分析层

使用 `utils/llm.py`（temperature=0.3），与阈值优化器共用相同接口。

### 输入（单次调用）

- 候选模式列表（词条、频次、分数、3 条样本）
- 现有监控规则摘要（5 条规则的描述）
- 已知噪音规则列表

### 输出 JSON schema

```json
{
  "patterns": [
    {
      "term": "原始词条",
      "type": "new_rule | noise | watch",
      "priority": "high | medium | low",
      "description": "一句话描述这是什么问题",
      "root_cause": "可能根因（仅 high/medium 给出，low 和 noise 省略）",
      "suggested_phrases": ["建议用于 ES 查询的关键词列表"],
      "reason": "为什么这样分类"
    }
  ]
}
```

`type` 枚举：
- `new_rule`：值得新建监控规则，`suggested_phrases` 可直接参考加入 `log_monitor.py`
- `noise`：已知或可预期的系统噪音，建议加入噪音过滤
- `watch`：不确定，建议人工关注但不立即建规则

`priority` 仅对 `new_rule` 有意义：
- `high`：频次高且显著性分数高，影响业务，需尽快建规则
- `medium`：有一定频次，值得关注
- `low`：偶发，可观察

---

## 4. 噪音规则库

### config/noise_rules.json

```json
{
  "_comment": "已确认的日志噪音规则，运行时过滤用。must_phrases 均匹配才算命中。",
  "rules": [
    {
      "id": "noise_001",
      "description": "RabbitMQ risk_task_fail_Delay exchange 缺 x-delayed-message 插件，持续报错，已知问题待开发修复",
      "must_phrases": ["risk_task_fail_Delay", "PRECONDITION_FAILED"],
      "confirmed_at": "2026-05-03",
      "confirmed_by": "operator"
    }
  ]
}
```

### monitor/log_monitor.py 修改

`_count_errors` 函数加载噪音规则，将匹配的规则转成 `must_not` 子句追加到 ES 查询：

```python
def _load_noise_must_not() -> list[dict]:
    path = Path(__file__).parent.parent / "config" / "noise_rules.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    result = []
    for rule in data.get("rules", []):
        phrases = rule.get("must_phrases", [])
        if phrases:
            # 每条规则：所有 phrases 同时匹配才过滤（must_not + bool.must）
            result.append({"bool": {"must": [
                {"match_phrase": {"message": p}} for p in phrases
            ]}})
    return result
```

`must_not` 子句在查询构造时注入，每条规则以 `bool.must` 包裹，确保只有**所有关键词同时命中**才过滤，不会因单个词误杀无关日志。

### tg_monitor.monitor_noise_rules 表

```sql
CREATE TABLE IF NOT EXISTS monitor_noise_rules (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  rule_type     VARCHAR(16)  NOT NULL COMMENT 'new_rule / noise',
  description   VARCHAR(256) NOT NULL,
  must_phrases  JSON         NOT NULL,
  priority      VARCHAR(16)  NULL     COMMENT 'high / medium / low（new_rule 用）',
  suggested_phrases JSON     NULL     COMMENT '建议监控关键词（new_rule 用）',
  confirmed_at  BIGINT       NOT NULL,
  confirmed_by  VARCHAR(64)  NOT NULL
);
```

---

## 5. 交互式 CLI

```
============================================================
发现模式 #2 / 8   [high] new_rule
词条: SQLIntegrityConstraintViolation for table 'tg_order'
24h 出现: 847 次   显著性分数: 12.4
------------------------------------------------------------
描述   tg_order 表唯一键冲突激增，超出历史基线 8 倍
根因   可能是重复提交或分布式事务未正确处理
建议词  ["SQLIntegrityConstraintViolation", "tg_order"]
------------------------------------------------------------
样本:
  [1] 2026-05-03 03:12:01  Duplicate entry 'ZF20260503ABCD' for key 'uk_trade_no'
  [2] 2026-05-03 03:11:58  Duplicate entry 'ZF20260503EFGH' for key 'uk_trade_no'
------------------------------------------------------------
[a] 记录为待建规则   [s] 跳过   [q] 保存退出
>
```

```
------------------------------------------------------------
发现模式 #5 / 8   [low] noise
词条: HikariPool-1 - Connection is not available
------------------------------------------------------------
描述   连接池等待超时，历史记录显示为定期低负载重连，属正常噪音
建议词  ["HikariPool", "Connection is not available"]
------------------------------------------------------------
[n] 确认为噪音   [s] 跳过   [q] 保存退出
>
```

- `[a]`：写入 `monitor_noise_rules`（rule_type=new_rule），终端提示建议词供开发参考
- `[n]`：写入 `noise_rules.json` + `monitor_noise_rules`（rule_type=noise），立即生效
- `watch` 类型只展示描述，无操作选项，自动进入下一条

**结束 summary：**
```
已记录待建规则 2 条，确认噪音 1 条，跳过 5 条
noise_rules.json 已更新
```

### 运行方式

```bash
# 完整分析（ES 聚合 + LLM + CLI）
python -m engine.log_analyzer

# 使用上次缓存（不重新调 LLM）
python -m engine.log_analyzer --from-cache

# 仅聚合层，打印候选模式，不调 LLM
python -m engine.log_analyzer --agg-only
```

---

## 6. 缓存机制

LLM 分析结果写入 `output/log_analyzer_cache_YYYYMMDD.json`，`--from-cache` 读取最新文件，避免重复 LLM 调用。

---

## 7. 调度

`scheduler.py` 注册每日 04:00 非交互分析 job（只跑 ES 聚合 + LLM，写缓存，不弹 CLI）：

```python
scheduler.add_job(job_run_log_analyzer, "cron",
                  hour=4, minute=0,
                  id="log_analyzer", max_instances=1)
```

---

## 8. 文件清单

| 操作 | 文件 | 说明 |
|---|---|---|
| 新建 | `engine/log_analyzer.py` | ES 聚合 + LLM 分析 + CLI |
| 新建 | `config/noise_rules.json` | 噪音规则库（含已知 RabbitMQ 问题） |
| 新建 | `db/migrate_log_analyzer.sql` | monitor_noise_rules 表迁移脚本 |
| 新建 | `tests/test_log_analyzer.py` | 聚合层 + 噪音过滤单元测试 |
| 修改 | `monitor/log_monitor.py` | `_count_errors` 加载噪音过滤 |
| 修改 | `scheduler.py` | 注册每日 04:00 分析 job |
| 修改 | `db/init_monitor.sql` | 补充 monitor_noise_rules 表定义 |

---

## 9. 依赖

```
elasticsearch  # 已有
langchain      # 已有（Phase 4 阈值优化器引入）
langchain-openai  # 已有
```

无新依赖。
