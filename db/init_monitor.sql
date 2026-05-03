CREATE DATABASE IF NOT EXISTS tg_monitor DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE tg_monitor;

CREATE TABLE IF NOT EXISTS monitor_action_queue (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  domain       VARCHAR(32)   NOT NULL COMMENT '监控域: payment/game/risk/activity/account/operation',
  action_type  VARCHAR(64)   NOT NULL COMMENT '动作类型: switch_channel/freeze_order/alert...',
  target_id    VARCHAR(64)   NULL     COMMENT '操作对象ID（渠道ID、订单号等）',
  payload      JSON          NOT NULL COMMENT '执行参数',
  status       TINYINT       NOT NULL DEFAULT 0 COMMENT '0-待审批 1-已批准 2-已执行 3-已拒绝',
  priority     TINYINT       NOT NULL DEFAULT 2 COMMENT '1-紧急 2-普通 3-低优先级',
  triggered_by VARCHAR(128)  NOT NULL COMMENT '触发指标描述',
  metric_value DECIMAL(18,4) NULL     COMMENT '触发时的指标值',
  threshold    DECIMAL(18,4) NULL     COMMENT '触发时的阈值',
  operator     VARCHAR(64)   NULL     COMMENT '审批人',
  approved_at  BIGINT        NULL,
  executed_at  BIGINT        NULL,
  created_at   BIGINT        NOT NULL,
  updated_at   BIGINT        NOT NULL
);

CREATE TABLE IF NOT EXISTS monitor_metric_history (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  domain      VARCHAR(32)   NOT NULL,
  metric      VARCHAR(64)   NOT NULL,
  channel_id  INT           NULL,
  value       DECIMAL(18,4) NOT NULL,
  level       VARCHAR(16)   NOT NULL,
  recorded_at BIGINT        NOT NULL,
  INDEX idx_metric_time (metric, recorded_at),
  INDEX idx_domain_time (domain, recorded_at),
  INDEX idx_level (level)
);
