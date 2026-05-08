USE tg_monitor;

CREATE TABLE IF NOT EXISTS monitor_suggestions (
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
  UNIQUE KEY uq_source_date_key (source, analysis_date, metric_key),
  INDEX idx_status (status),
  INDEX idx_source_status (source, status)
);
