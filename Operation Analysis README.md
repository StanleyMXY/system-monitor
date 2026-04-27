# 游戏平台智能运营系统

博彩游戏平台全链路运营系统，覆盖流量获取 → 注册转化 → 活动运营 → 用户留存。每日自动运行，将原始数据转化为平台日报、渠道分析报告和投放决策建议。

产品背景详见 [PRODUCT_OVERVIEW.md](PRODUCT_OVERVIEW.md)。

---

## 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.10+ |
| 数据库 | MySQL（PyMySQL） |
| LLM | Qwen3-max（OpenAI 兼容接口） |
| 调度 | APScheduler |
| Telegram | Telethon（数据采集）/ Bot API（通知） |
| 报告 | ECharts 5.4.3 + HTML5 暗色主题 |
| 预算执行 | Browser Use + langchain-anthropic |

---

## 项目结构

```
├── etl/                  # 数据清洗与入库（12步 ETL）
├── ops_reports/          # 平台报告（daily/weekly/monthly：metrics + prompt + AI + HTML + Markdown + snapshot）
├── campaign/             # 渠道 ROI 计算引擎
├── decision/             # 决策引擎（7层规则 + LLM审计 + 执行解析）
├── jobs/                 # 每日任务入口
│   ├── run_ops.py        # 平台日报
│   ├── run_channel.py    # 渠道 Cohort 分析
│   ├── run_promo_review.py  # 活动复盘
│   └── auto_ops_loop.py  # 自动研究循环
├── backtest/             # 策略回测引擎（Sharpe / 准确率 / 最大回撤）
├── budget/               # 预算执行适配器（TG / Facebook / Google Ads）
├── knowledge/            # 平台知识记忆库
├── notify/               # Telegram 通知模块
├── promo_review/         # 活动复盘报告
├── media/                # Telegram 频道数据采集
├── db/
│   ├── schema/           # 建库 SQL（完整版）
│   └── migrations/       # 迁移补丁（v4~v9）
├── config.py             # 配置管理（读取 .env）
├── db.py                 # MySQL 连接工具
├── scheduler.py          # 生产调度器
└── main.py               # 合并入口（ops + channel）
```

---

## 安装

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

复制环境变量模板并填写配置：

```bash
cp .env.example .env
```

主要配置项（`.env`）：

```ini
# 数据库
HOST=127.0.0.1
PORT=3306
USER=your_user
PASSWORD=your_password
DATABASE=game_tg_analysis   # 或 game_q6_analysis

# LLM（OpenAI 兼容接口）
LLM_API_KEY=your_key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen3-max

# Telegram 通知（可选）
TG_BOT_TOKEN=your_bot_token
TG_CHAT_ID=-100xxxxxxxxx           # 日报推送的群组/频道（逗号分隔多个）
TG_NOTIFY_ENABLED=true
TG_APPROVE_ENABLED=false           # true = 预算执行需人工审批

# 执行等级
EXECUTION_TIER=1                   # 1=只读报告  2=规则自动化  3=智能自适应

# S3 报告存储（可选，留空则不上传）
S3_ENDPOINT=https://s3.ap-southeast-1.amazonaws.com
S3_BUCKET=your_bucket
S3_ACCESS_KEY=
S3_SECRET_KEY=
```

---

## 建库

```bash
# 初次建库（执行完整 schema，会 DROP 已有表，仅初始化时使用）
mysql -u user -p < db/schema/game_tg_analysis.sql
mysql -u user -p < db/schema/game_q6_analysis.sql

# 已有库升级（打补丁，幂等安全）
mysql -u user -p < db/migrations/models_v9_patch_s3.sql
```

---

## 运行

### ETL（数据清洗入库）

```bash
# TG Game
python -m etl.run_etl --game tg --date 2026-04-16

# Q Game（本地镜像）
python -m etl.run_etl --game q --date 2026-04-16 --file etl/etl_q_game_local.sql

# 只跑部分步骤
python -m etl.run_etl --game tg --steps 0 1 2 3

# 容错模式（汇总所有错误，不中途退出）
python -m etl.run_etl --game tg --continue-on-error
```

### 每日任务

```bash
# 平台日报 / 周报 / 月报
python -m jobs.run_ops --date 2026-04-16                       # 默认 daily
python -m jobs.run_ops --mode weekly  --date 2026-04-12        # 对齐到所在周
python -m jobs.run_ops --mode monthly --date 2026-04-15        # 对齐到所在月
python -m jobs.run_ops --mode weekly  --backfill               # 回填，静音通知

# 渠道 Cohort 分析
python -m jobs.run_channel --date 2026-04-16

# 活动复盘
python -m jobs.run_promo_review

# 自动研究循环
python -m jobs.auto_ops_loop
```

### 生产调度（完整流水线）

```bash
python scheduler.py --run all            # 完整流程
python scheduler.py --run ops            # 仅平台日报
python scheduler.py --run ops-weekly     # 手动触发平台周报
python scheduler.py --run ops-monthly    # 手动触发平台月报
python scheduler.py --run channel        # 仅渠道分析
python scheduler.py --run budget --dry-run   # 预算执行预演
```

调度时间（Asia/Shanghai）：

```
03:00         ETL TG Game
04:00         平台日报（daily）
04:10         自动研究循环
04:30         渠道 Cohort 分析
05:30         预算执行
06:30（周一） 平台周报（ops_weekly）
06:50（1日）  平台月报（ops_monthly）
```

### 回测

```bash
python backtest/backtest_engine.py --start 2026-01-01 --end 2026-03-01 --verify-days 7
```

---

## 验证部署

```bash
# 1. ETL 试运行（不写入数据）
python -m etl.run_etl --game tg --date 2026-03-13 --dry-run
python -m etl.run_etl --game q  --date 2025-11-20 --dry-run

# 2. 实际写入
python -m etl.run_etl --game tg --date 2026-03-13
python -m etl.run_etl --game q  --date 2025-11-20

# 3. 验证日报生成
python -m jobs.run_ops --date 2026-03-13

# 4. 验证渠道分析
python -m jobs.run_channel --date 2026-03-13

# 5. 单测套件（96项）
pytest tests/
```

---

## 输出

- **日报**：`output/OpsReport_YYYY-MM-DD_*.html` + `output/ops_report_daily_YYYY-MM-DD_*.md`
- **周报**：`output/ops_report_weekly_YYYY-Www_*.{html,md}`
- **月报**：`output/ops_report_monthly_YYYY-MM_*.{html,md}`
- **渠道报告 HTML**：`output/Report_{channel}_*.html`
- **活动复盘 HTML**：`output/PromoReview_{promo}_*.html`
- **运行日志**：`logs/`

---

## 支持的游戏平台

| 平台 | 源库 | 分析库 |
|------|------|--------|
| TG Game | `perfine_helper` | `game_tg_analysis` |
| Q Game | `swan_helper` / `etl_source_mirror` | `game_q6_analysis` |
