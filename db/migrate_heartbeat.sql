-- 调度器心跳表（单行，id=1 固定）
-- 用于 watchdog.py 检测 scheduler.py 是否存活
CREATE TABLE IF NOT EXISTS scheduler_heartbeat (
  id           TINYINT UNSIGNED NOT NULL DEFAULT 1,
  last_beat_at DATETIME         NOT NULL,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
