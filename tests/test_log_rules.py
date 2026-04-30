from engine.models import MetricResult
from engine.rule_engine import evaluate

THRESHOLDS = {
    "high_priority": {
        "game_launch_error": {"count_warning": 3, "count_critical": 10},
        "mq_route_error": {"count_warning": 5, "count_critical": 20},
        "db_shard_error": {"count_warning": 1, "count_critical": 5},
    },
    "low_priority": {
        "websocket_error": {"count_warning": 20},
        "db_duplicate_error": {"count_warning": 10},
    },
}


def _m(metric, value, extra=None):
    return MetricResult(domain="log", metric=metric, value=value, extra=extra or {})


# game_launch_error
def test_game_launch_error_ok():
    result = evaluate([_m("game_launch_error_count", 2.0)], THRESHOLDS)[0]
    assert result.level == "ok"

def test_game_launch_error_warning():
    result = evaluate([_m("game_launch_error_count", 3.0)], THRESHOLDS)[0]
    assert result.level == "warning"
    assert result.action == "alert"

def test_game_launch_error_critical():
    result = evaluate([_m("game_launch_error_count", 10.0)], THRESHOLDS)[0]
    assert result.level == "critical"
    assert result.action == "enqueue"

# mq_route_error
def test_mq_route_error_ok():
    result = evaluate([_m("mq_route_error_count", 4.0)], THRESHOLDS)[0]
    assert result.level == "ok"

def test_mq_route_error_warning():
    result = evaluate([_m("mq_route_error_count", 5.0)], THRESHOLDS)[0]
    assert result.level == "warning"
    assert result.action == "alert"

def test_mq_route_error_critical():
    result = evaluate([_m("mq_route_error_count", 20.0)], THRESHOLDS)[0]
    assert result.level == "critical"
    assert result.action == "enqueue"

# db_shard_error
def test_db_shard_error_ok():
    result = evaluate([_m("db_shard_error_count", 0.0)], THRESHOLDS)[0]
    assert result.level == "ok"

def test_db_shard_error_warning():
    result = evaluate([_m("db_shard_error_count", 1.0)], THRESHOLDS)[0]
    assert result.level == "warning"
    assert result.action == "alert"

def test_db_shard_error_critical():
    result = evaluate([_m("db_shard_error_count", 5.0)], THRESHOLDS)[0]
    assert result.level == "critical"
    assert result.action == "enqueue"

# websocket_error
def test_websocket_error_ok():
    result = evaluate([_m("websocket_error_count", 19.0)], THRESHOLDS)[0]
    assert result.level == "ok"

def test_websocket_error_warning():
    result = evaluate([_m("websocket_error_count", 20.0)], THRESHOLDS)[0]
    assert result.level == "warning"
    assert result.action == "alert"

# db_duplicate_error
def test_db_duplicate_error_ok():
    result = evaluate([_m("db_duplicate_error_count", 9.0)], THRESHOLDS)[0]
    assert result.level == "ok"

def test_db_duplicate_error_warning():
    result = evaluate([_m("db_duplicate_error_count", 10.0)], THRESHOLDS)[0]
    assert result.level == "warning"
    assert result.action == "alert"
