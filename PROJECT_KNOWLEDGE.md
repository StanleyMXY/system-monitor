# 天工平台系统运营监控 — Project Knowledge

> 本文档是本仓库的完整技术参考，面向新接手开发者或跨会话的 AI 协作者。

---

## 1. 项目定位

**天工平台实时系统监控**——对天工平台（tian-gong）生产库进行周期性 SQL 采集，经规则引擎评估后输出告警和动作队列，覆盖支付、游戏供应商、风控、活动、账户、系统操作六个域，常驻运行。

**与 TG-Ads-Analysis 的关系：** TG-Ads-Analysis（兄弟项目）是离线分析系统，每日批跑 ETL + AI 日报，面向运营决策。本项目是其衍生独立系统，架构与之解耦（独立库、独立调度、独立告警），但继承了读写分库、APScheduler 异步调度、PyMySQL 技术栈等方法论。二者数据源不同：TG-Ads-Analysis 读取的是结构经过 ETL 整理的报表分析库，本系统直接读取 tian-gong 生产业务库。

---

## 2. 完整文件结构

```
System Monitor/
├── config/
│   ├── __init__.py
│   ├── db.py                       # 数据库连接工厂（读写分库）
│   ├── thresholds_payment.json     # 支付域阈值配置
│   ├── thresholds_game.json        # 游戏供应商域阈值配置
│   ├── thresholds_risk.json        # 风控域阈值配置
│   ├── thresholds_activity.json    # 活动域阈值配置
│   ├── thresholds_account.json     # 用户账户域阈值配置
│   └── thresholds_operation.json   # 系统操作域阈值配置
│
├── engine/
│   ├── __init__.py
│   ├── models.py                   # 核心数据结构（MetricResult / RuleResult）
│   ├── threshold_config.py         # 阈值 JSON 加载器
│   ├── rule_engine.py              # 规则引擎（19 条规则，全域覆盖）
│   └── alert_engine.py             # 告警引擎（日志 + logs/alerts.log）
│
├── monitor/
│   ├── __init__.py
│   ├── payment_monitor.py          # 支付域：充值/余额/提现采集
│   ├── game_monitor.py             # 游戏供应商域：余额转账/对账采集
│   ├── risk_monitor.py             # 风控域：预警积压/超时/黑名单采集
│   ├── activity_monitor.py         # 活动域：兑换/首充采集
│   ├── account_monitor.py          # 账户域：冻结余额增长率采集（in-memory）
│   └── operation_monitor.py        # 系统操作域：VIP调整/余额调整/配置变更采集
│
├── executor/
│   ├── __init__.py
│   └── action_executor.py          # 执行层：写 tg_monitor.monitor_action_queue
│
├── dashboard/
│   ├── __init__.py
│   └── monitor_dashboard.py        # 实时看板（HTTP server + 动态 HTML）
│
├── tests/
│   ├── __init__.py
│   ├── test_db.py
│   ├── test_threshold_config.py
│   ├── test_rule_engine.py         # Phase 1 规则测试
│   ├── test_rule_engine_phase2.py  # Phase 2 规则测试
│   ├── test_phase3_rules.py        # Phase 3 规则测试
│   ├── test_alert_engine.py
│   ├── test_payment_monitor.py
│   ├── test_game_monitor.py
│   ├── test_risk_monitor.py
│   ├── test_activity_monitor.py
│   ├── test_account_monitor.py
│   ├── test_operation_monitor.py
│   ├── test_action_executor.py
│   └── test_monitor_dashboard.py
│
├── docs/superpowers/
│   ├── specs/2026-04-25-phase1-design.md
│   ├── specs/2026-04-27-phase2-design.md
│   ├── specs/2026-04-28-phase3-design.md
│   ├── plans/2026-04-25-phase1-implementation.md
│   ├── plans/2026-04-27-phase2-implementation.md
│   └── plans/2026-04-28-phase3-implementation.md
│
├── output/
│   └── monitor_dashboard.html      # 看板静态文件（运行时自动生成/更新）
│
├── logs/
│   └── alerts.log                  # 结构化告警日志（JSON Lines）
│
├── scheduler.py                    # 主入口：APScheduler 常驻调度器
├── smoke_test_phase3.py            # Phase 3 冒烟测试脚本（对接真实库）
├── .env                            # 本地环境变量（不入库）
└── .env.example                    # 环境变量模板
```

---

## 3. 数据库连接配置

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
```

### 连接工厂（config/db.py）

| 函数 | 库 | 用途 |
|------|----|------|
| `get_source_conn()` | `tian-gong`（只读） | Monitor 层 SQL 采集 |
| `get_monitor_conn()` | `tg_monitor`（读写） | enqueue_action 写入队列 |

分库原因：tian-gong 是生产库，监控写操作不得增加其压力；读写独立，单轮采集额外延迟 < 5ms。

---

## 4. 监控数据库表结构

### 建库 SQL

```sql
CREATE DATABASE IF NOT EXISTS tg_monitor DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### monitor_action_queue（核心队列表）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | BIGINT AUTO_INCREMENT | 主键 |
| `domain` | VARCHAR(32) | 监控域，如 `payment` |
| `action_type` | VARCHAR(64) | 动作类型，如 `switch_channel` |
| `target_id` | VARCHAR(64) NULL | 目标 ID（如渠道 ID） |
| `payload` | JSON | 指标上下文（metric、value、extra） |
| `status` | TINYINT | `0`=待审批 `1`=已批准 `2`=已执行 `3`=已拒绝 |
| `priority` | TINYINT | `1`=紧急（critical） `2`=普通 `3`=低 |
| `triggered_by` | VARCHAR(128) | 告警消息原文 |
| `metric_value` | DECIMAL(18,4) NULL | 触发时的指标值 |
| `threshold` | DECIMAL(18,4) NULL | 对应阈值 |
| `operator` | VARCHAR(64) NULL | 审批操作人（写入时为 NULL） |
| `approved_at` | BIGINT NULL | 审批时间（Unix 秒） |
| `executed_at` | BIGINT NULL | 执行时间（Unix 秒） |
| `created_at` | BIGINT NOT NULL | 创建时间（Unix 秒） |
| `updated_at` | BIGINT NOT NULL | 更新时间（Unix 秒） |

> **时间戳统一规则：** `monitor_action_queue` 使用 Unix 秒（`int(time.time())`）。源库各表时间戳格式不同，监控采集时一律换算为毫秒再传入 SQL（`* 1000`），`sys_operation_log` 例外，其 `created_at` 是 datetime 列，用 `FROM_UNIXTIME()` 转换。

---

## 5. 六个监控域说明

### 5.1 支付域（payment）

**源库表：** `pay_recharge`、`pay_channel_account`、`pay_channel`、`pay_withdraw`

| 采集函数 | 监控对象 | 核心指标 | 阈值来源 |
|----------|----------|----------|----------|
| `check_recharge` | 各渠道充值，过去 1 小时 | 成功率 / 超时率 / 积压数 | `recharge.*` |
| `check_channel_balance` | 所有渠道账号余额 | 当前余额（元） | `channel_account.balance_warning_amount` |
| `check_withdraw_queue` | 提现审核积压（queue_timeout_hours 外未处理） | 积压数 | `withdraw.queue_count_warning` |
| `check_withdraw_fail_rate` | 过去 1 小时提现失败率（status=4/已处理） | 失败率 | `withdraw.fail_rate_warning` |

**告警级别：** `recharge_success_rate < 0.60` → critical，其余 → warning

---

### 5.2 游戏供应商域（game）

**源库表：** `gam_balance_transfer`、`sys_manuf_reconciliation_daily`

| 采集函数 | 监控对象 | 核心指标 | 阈值来源 |
|----------|----------|----------|----------|
| `check_balance_transfer` | 各供应商余额转账，过去 1 小时 | 失败率 / 重试异常订单数 | `balance_transfer.*` |
| `check_reconciliation` | 各供应商对账差异，过去 7 天 | 连续差异天数（reconcile_status=2） | `reconciliation.diff_consecutive_days_critical` |

**说明：** 供应商维度用 `vendor_id / provider_code` 标识（非 manufacturer_id）。`check_reconciliation` 通过 cron 每日 02:00 单次执行。

---

### 5.3 风控域（risk）

**源库表：** `rsk_alert`、`rsk_event`、`rsk_blacklist`

| 采集函数 | 监控对象 | 核心指标 | 阈值来源 |
|----------|----------|----------|----------|
| `check_alert_backlog` | 高危预警积压（alert_level≥3，handle_status=0） | 积压数 | `alert.high_risk_pending_warning`(10) |
| `check_alert_timeout` | 高危预警超时未处理（超 high_risk_timeout_hours） | 超时数 | > 0 即告警 |
| `check_event_backlog` | 高危风控事件积压（event_level=3，handle_status=0） | 积压数 | `event.high_risk_pending_warning`(5) |
| `check_blacklist_expiry` | 临时黑名单即将到期（expire_type=2） | 到期数 | > 0 且在 expiry_reminder_hours 内 |

---

### 5.4 活动域（activity）

**源库表：** `act_activity_redemption`、`act_first_deposit_record`

| 采集函数 | 监控对象 | 核心指标 | 阈值来源 |
|----------|----------|----------|----------|
| `check_redemption` | 活动兑换，窗口期内（check_interval_minutes） | 失败率 | `redemption.fail_rate_warning`(0.05) |
| `check_first_deposit` | 首充记录，窗口期内 | 异常条目数 | `first_deposit.fail_alert_threshold`(1) |

---

### 5.5 用户账户域（account）

**源库表：** `usr_account`

| 采集函数 | 监控对象 | 核心指标 | 阈值来源 |
|----------|----------|----------|----------|
| `check_frozen_balance` | 全平台冻结余额 | 与上一轮比较的增长率 | `frozen_balance.growth_rate_warning`(0.50) |

**特殊设计：** 采用 in-memory snapshot（模块级 `_prev_frozen` 变量）在相邻两次采集间计算增长率，无需数据库历史表。首次采集返回增长率 0 作为基准。

---

### 5.6 系统操作域（operation）

**源库表：** `adm_vip_adjust_log`、`pay_balance_adjustment`、`sys_operation_log`

| 采集函数 | 监控对象 | 核心指标 | 阈值来源 |
|----------|----------|----------|----------|
| `check_vip_adjust` | 过去 1 小时 VIP 调整操作次数 | 次数 | `vip_adjust.batch_count_per_hour_warning`(20) |
| `check_balance_adjustment` | 窗口期内大额余额调整（abs(amount)≥large_amount_threshold） | 笔数 | 大于等于 1 笔即 warning + enqueue |
| `check_config_change` | 过去 1 小时 sys_operation_log 配置变更次数 | 次数 | `config_change.change_count_per_hour_warning`(10) |

> `check_config_change` 使用 `FROM_UNIXTIME()` 处理 datetime 类型 `created_at`，与其他表的 ms 时间戳不同。

---

## 6. 调度器说明

**入口文件：** `scheduler.py`  
**运行方式：** `python scheduler.py`（常驻进程）  
**时区：** Asia/Shanghai  
**框架：** `APScheduler AsyncIOScheduler` + `asyncio.run()`

每个 job 都是 `async` 函数，内部用 `asyncio.get_running_loop().run_in_executor(None, ...)` 将同步采集推入线程池，避免阻塞事件循环。

### 全量调度表

| job id | 采集函数 | 触发方式 | 频率/时间 |
|--------|----------|----------|-----------|
| `payment_recharge` | `check_recharge` | interval | **5 分钟** |
| `payment_channel_balance` | `check_channel_balance` | interval | 15 分钟 |
| `payment_withdraw_queue` | `check_withdraw_queue` | interval | 15 分钟 |
| `payment_withdraw_fail_rate` | `check_withdraw_fail_rate` | interval | 60 分钟 |
| `game_balance_transfer` | `check_balance_transfer` | interval | 10 分钟 |
| `game_reconciliation` | `check_reconciliation` | cron | **每日 02:00** |
| `risk_alert_backlog` | `check_alert_backlog` | interval | 10 分钟 |
| `risk_alert_timeout` | `check_alert_timeout` | interval | 30 分钟（固定） |
| `risk_event_backlog` | `check_event_backlog` | interval | 10 分钟 |
| `risk_blacklist_expiry` | `check_blacklist_expiry` | interval | 60 分钟 |
| `activity_redemption` | `check_redemption` | interval | 15 分钟 |
| `activity_first_deposit` | `check_first_deposit` | interval | 5 分钟 |
| `account_frozen_balance` | `check_frozen_balance` | interval | 60 分钟 |
| `operation_vip_adjust` | `check_vip_adjust` | interval | 60 分钟 |
| `operation_balance_adjustment` | `check_balance_adjustment` | interval | 10 分钟 |
| `operation_config_change` | `check_config_change` | interval | 60 分钟 |

所有 interval 频率从对应域 JSON 的 `check_interval_minutes` 读取（`game_reconciliation` 和 `risk_alert_timeout` 除外，为硬编码 cron/固定值）。

---

## 7. 阈值配置文件体系

### 文件结构

每个域对应一个 JSON 文件，位于 `config/thresholds_{domain}.json`：

```
config/thresholds_payment.json
config/thresholds_game.json
config/thresholds_risk.json
config/thresholds_activity.json
config/thresholds_account.json
config/thresholds_operation.json
```

### JSON 约定

- 顶层 `_comment`（以 `_` 开头）为元信息，加载时自动过滤
- 每个 sub-key 对应一个监控子类型（如 `recharge`、`withdraw`）
- `check_interval_minutes` 是必需键，调度器从此读取触发频率
- 单位约定写在 `_comment` 中（比率：0–1；金额：元；时间：分钟或小时）

### 加载方式（engine/threshold_config.py）

```python
def load_thresholds(domain: str) -> dict:
    path = CONFIG_DIR / f"thresholds_{domain}.json"
    with open(path, encoding="utf-8") as f:
        config = json.load(f)
    return {k: v for k, v in config.items() if not k.startswith("_")}
```

调度器在启动时为每个域预加载一次，job 执行时也通过 `_run_monitor` 再次加载。

### 各域关键阈值速查

| 域 | 关键阈值 | 默认值 |
|----|----------|--------|
| payment | 充值成功率 critical | 60% |
| payment | 充值成功率 warning | 80% |
| payment | 渠道账号余额 warning | 10,000 元 |
| payment | 提现积压 warning | 50 条 |
| payment | 提现失败率 warning | 20% |
| game | 转账失败率 warning | 5% |
| game | 对账差异连续天数 critical | 2 天 |
| risk | 高危预警积压 warning | 10 条 |
| risk | 高危事件积压 warning | 5 条 |
| activity | 兑换失败率 warning | 5% |
| activity | 首充异常数 warning | ≥1 条 |
| account | 冻结余额增长率 warning | 50% |
| operation | VIP 批量调整 warning | 20 次/小时 |
| operation | 大额余额调整 warning | ≥1 笔（单笔 ≥10,000 元） |
| operation | 配置变更频率 warning | 10 次/小时 |

---

## 8. 执行层设计

### 数据流全貌

```
Monitor 采集层（SQL）
    ↓  list[MetricResult]
规则引擎（rule_engine.evaluate）
    ↓  list[RuleResult]（level / action）
        ├── alert_engine.handle  → logs/alerts.log（所有 warning+）
        ├── dashboard.update     → output/monitor_dashboard.html
        └── action_executor.enqueue → monitor_action_queue（仅 action="enqueue"）
```

### 自动执行 vs 人工审批边界

| action 值 | 触发条件 | 写入队列 | 说明 |
|-----------|----------|----------|------|
| `none` | 指标正常（ok） | 否 | 无动作 |
| `alert` | warning — 告警但无需动作 | 否 | 仅写日志和看板 |
| `enqueue` | critical 或高风险 warning | **是** | status=0（待审批），等人工介入 |

**当前触发 enqueue 的规则（共 4 条）：**
- `recharge_success_rate` critical（< 60%）→ 切渠道候机
- `game_reconciliation_diff_days` critical（≥ 2 天）→ 对账差异处理
- `vip_adjust_count` warning（> 20 次/小时）→ 批量调整复查
- `balance_adjustment_large_count` warning（≥1 笔）→ 大额调整确认

人工审批后将 `status` 从 0 改为 1（批准）或 3（拒绝），执行完成后改为 2。

---

## 9. 关键设计决策

### 为什么独立部署而非嵌入 TG-Ads-Analysis？

TG-Ads-Analysis 是异步批跑系统，每日执行一次，进程完成后退出。本系统是常驻轮询进程，需要 24/7 运行、每 5 分钟触发，生命周期完全不同。合并会使两个系统互相干扰（日报任务拖慢监控轮次，监控进程长期占用内存）。

### 为什么规则引擎主导，而非 AI 实时判断？

实时监控需要毫秒级确定性响应，AI 推理有延迟（通常 1–5 秒/次）且成本高。规则引擎对 19 条业务规则做硬编码阈值判断，延迟 < 1ms，可审计，误报来源可精确定位。AI 的角色在 Phase 4 中定位为**离线阈值优化器**：周期性分析历史告警数据，建议更新阈值，由运营人工确认后写回 JSON 文件——不参与实时决策路径。

### AI 的角色定位

```
AI（Phase 4，离线）
  ↓ 分析 alerts.log + monitor_action_queue 历史
  ↓ 建议阈值调整（如"渠道 A 的 success_rate_warning 应降至 0.75"）
  ↓ 输出报告供人工审核
  ↓ 人工确认 → 修改 thresholds_{domain}.json
                       ↑ 规则引擎实时读取
```

规则引擎不依赖 AI，AI 不参与实时路径，两者完全解耦。

### 读写分库的必要性

`tian-gong` 是 300+ 张表的生产业务库，任何 INSERT/UPDATE 都可能触发行锁或影响慢查询阈值。监控系统的读操作用只读账号，写动作（enqueue_action）落到独立的 `tg_monitor` 库，从源头杜绝监控影响业务。

---

## 10. 待完成事项（Phase 4）

| 项目 | 描述 | 优先级 |
|------|------|--------|
| ES 日志接入 | xftapi 日志盲区（回调 IP 白名单拒绝、接口慢请求）需要通过 Elasticsearch 日志采集。ES 白名单待开通。接入后作为第 7 个监控域。 | P1 |
| AI 阈值优化器 | `engine/threshold_optimizer.py`，定期分析 `logs/alerts.log` 和 `monitor_action_queue`，给出阈值调整建议报告。 | P2 |
| Telegram 告警通知 | 在 `engine/alert_engine.py` 增加 TG Bot 推送（接口已预留），高危告警实时推送运营群。 | P2 |
| 告警降噪 / 聚合 | 同一渠道/指标短期内重复告警合并，避免告警风暴。 | P3 |

---

## 11. 常用命令速查

### 启动监控调度器

```bash
python scheduler.py
# 看板访问地址：http://localhost:8080/monitor_dashboard.html
```

### 运行测试套件

```bash
# 全量（81 项）
pytest tests/

# 仅 Phase 3 规则
pytest tests/test_phase3_rules.py -v

# 特定域
pytest tests/test_payment_monitor.py -v
```

### 冒烟测试（对接真实库）

```bash
python smoke_test_phase3.py
```

### 查看告警日志

```bash
# 查看最新 50 条告警（JSON Lines 格式）
tail -n 50 logs/alerts.log

# 过滤 critical
grep '"level": "critical"' logs/alerts.log
```

### 查看动作队列（待审批）

```sql
-- 在 tg_monitor 库执行
SELECT id, domain, action_type, triggered_by, metric_value, threshold, created_at
FROM monitor_action_queue
WHERE status = 0
ORDER BY priority, created_at;
```

### 审批动作

```sql
UPDATE monitor_action_queue SET status = 1, operator = 'your_name', approved_at = UNIX_TIMESTAMP() WHERE id = ?;
-- 拒绝：status = 3
```

### 调整阈值（示例）

```bash
# 修改支付域充值成功率预警线为 75%
# 编辑 config/thresholds_payment.json，将 success_rate_warning 改为 0.75
# 调度器下一轮会自动读取新值（每次 _run_monitor 都重新 load_thresholds）
```

---

## 附录：核心数据结构

```python
@dataclass
class MetricResult:
    domain: str          # "payment" | "game" | "risk" | "activity" | "account" | "operation"
    metric: str          # 指标名，如 "recharge_success_rate"
    value: float         # 当前指标值
    channel_id: int | None = None  # 渠道/供应商 ID（无维度时为 None）
    extra: dict = field(default_factory=dict)  # 附加上下文

@dataclass
class RuleResult:
    level: Literal["ok", "warning", "critical"]
    action: Literal["none", "alert", "enqueue"]
    metric: MetricResult
    threshold: float     # 触发时对应的阈值
    message: str         # 人类可读的告警说明
```
