from unittest.mock import MagicMock
from monitor.log_monitor import (
    check_game_launch_error,
    check_mq_route_error,
    check_db_shard_error,
    check_websocket_error,
    check_db_duplicate_error,
)

THRESHOLDS = {
    "high_priority": {
        "check_interval_minutes": 5,
        "game_launch_error": {"count_warning": 3, "count_critical": 10},
        "mq_route_error": {"count_warning": 5, "count_critical": 20},
        "db_shard_error": {"count_warning": 1, "count_critical": 5},
    },
    "low_priority": {
        "check_interval_minutes": 30,
        "websocket_error": {"count_warning": 20},
        "db_duplicate_error": {"count_warning": 10},
    },
}


def _make_es(count, sample_message=""):
    es = MagicMock()
    es.count.return_value = {"count": count}
    es.search.return_value = {
        "hits": {
            "hits": [{"_source": {"message": sample_message}}] if sample_message else []
        }
    }
    return es


def test_check_game_launch_error_returns_one_metric():
    es = _make_es(5, "LaunchController: 启动游戏 xxx\njava.io.IOException: 500")
    results = check_game_launch_error(es, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "game_launch_error_count"
    assert results[0].domain == "log"
    assert results[0].value == 5.0


def test_check_game_launch_error_zero():
    es = _make_es(0)
    results = check_game_launch_error(es, THRESHOLDS)
    assert results[0].value == 0.0


def test_check_mq_route_error_returns_one_metric():
    es = _make_es(12)
    results = check_mq_route_error(es, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "mq_route_error_count"
    assert results[0].value == 12.0


def test_check_db_shard_error_returns_one_metric():
    es = _make_es(2)
    results = check_db_shard_error(es, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "db_shard_error_count"
    assert results[0].value == 2.0


def test_check_websocket_error_returns_one_metric():
    es = _make_es(25)
    results = check_websocket_error(es, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "websocket_error_count"
    assert results[0].value == 25.0


def test_check_db_duplicate_error_returns_one_metric():
    es = _make_es(3)
    results = check_db_duplicate_error(es, THRESHOLDS)
    assert len(results) == 1
    assert results[0].metric == "db_duplicate_error_count"
    assert results[0].value == 3.0


def test_game_launch_error_extra_has_sample():
    es = _make_es(1, "LaunchController: 启动游戏 abc\njava.io.IOException")
    results = check_game_launch_error(es, THRESHOLDS)
    assert "sample" in results[0].extra
