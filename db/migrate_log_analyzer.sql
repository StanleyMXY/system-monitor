USE tg_monitor;

CREATE TABLE IF NOT EXISTS monitor_noise_rules (
  id                BIGINT AUTO_INCREMENT PRIMARY KEY,
  rule_type         VARCHAR(16)  NOT NULL COMMENT 'new_rule / noise',
  description       VARCHAR(256) NOT NULL,
  must_phrases      JSON         NOT NULL,
  priority          VARCHAR(16)  NULL     COMMENT 'high / medium / low（new_rule 用）',
  suggested_phrases JSON         NULL     COMMENT '建议监控关键词（new_rule 用）',
  confirmed_at      BIGINT       NOT NULL,
  confirmed_by      VARCHAR(64)  NOT NULL
);
