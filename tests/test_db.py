import sys


def test_get_source_conn_uses_source_env(monkeypatch):
    monkeypatch.setenv("SOURCE_HOST", "src-host")
    monkeypatch.setenv("SOURCE_PORT", "3307")
    monkeypatch.setenv("SOURCE_USER", "src_user")
    monkeypatch.setenv("SOURCE_PASSWORD", "src_pass")
    monkeypatch.setenv("SOURCE_DATABASE", "src_db")
    monkeypatch.setenv("MONITOR_HOST", "mon-host")
    monkeypatch.setenv("MONITOR_PORT", "3306")
    monkeypatch.setenv("MONITOR_USER", "mon_user")
    monkeypatch.setenv("MONITOR_PASSWORD", "mon_pass")
    monkeypatch.setenv("MONITOR_DATABASE", "mon_db")

    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return object()

    import pymysql
    monkeypatch.setattr(pymysql, "connect", fake_connect)

    if "config.db" in sys.modules:
        del sys.modules["config.db"]

    from config import db
    db.get_source_conn()

    assert captured["host"] == "src-host"
    assert captured["port"] == 3307
    assert captured["user"] == "src_user"
    assert captured["database"] == "src_db"


def test_get_monitor_conn_uses_monitor_env(monkeypatch):
    monkeypatch.setenv("SOURCE_HOST", "src-host")
    monkeypatch.setenv("SOURCE_PORT", "3306")
    monkeypatch.setenv("SOURCE_USER", "src_user")
    monkeypatch.setenv("SOURCE_PASSWORD", "src_pass")
    monkeypatch.setenv("SOURCE_DATABASE", "src_db")
    monkeypatch.setenv("MONITOR_HOST", "mon-host")
    monkeypatch.setenv("MONITOR_PORT", "3308")
    monkeypatch.setenv("MONITOR_USER", "mon_user")
    monkeypatch.setenv("MONITOR_PASSWORD", "mon_pass")
    monkeypatch.setenv("MONITOR_DATABASE", "mon_db")

    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return object()

    import pymysql
    monkeypatch.setattr(pymysql, "connect", fake_connect)

    if "config.db" in sys.modules:
        del sys.modules["config.db"]

    from config import db
    db.get_monitor_conn()

    assert captured["host"] == "mon-host"
    assert captured["port"] == 3308
    assert captured["database"] == "mon_db"
