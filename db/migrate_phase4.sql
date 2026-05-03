USE tg_monitor;

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
