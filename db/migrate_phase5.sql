USE tg_monitor;

CREATE TABLE IF NOT EXISTS monitor_root_cause_reports (
  id               BIGINT AUTO_INCREMENT PRIMARY KEY,
  analysis_date    DATE         NOT NULL COMMENT '分析日期',
  alert_count      INT          NOT NULL COMMENT '分析的告警条数',
  chain_count      INT          NOT NULL COMMENT '识别出的因果链数量',
  report_json      JSON         NOT NULL COMMENT '完整报告（含 LLM 输出）',
  suggested_chains JSON         NULL     COMMENT 'LLM 建议新增的 causal_chains 条目',
  created_at       BIGINT       NOT NULL,
  UNIQUE KEY uq_date (analysis_date)
);
