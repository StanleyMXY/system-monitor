# tests/test_api_suggestions.py
import json
import time
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from api.server import app


def _make_suggestion(id=1, source="threshold_optimizer", item_type="adjust",
                     status=0, metric_key="recharge_success_rate"):
    return {
        "id": id,
        "source": source,
        "analysis_date": "2026-05-08",
        "item_type": item_type,
        "metric_key": metric_key,
        "payload": json.dumps({"metric": metric_key, "suggested": {"warning": 0.85}}),
        "status": status,
        "reviewed_at": None,
        "reviewed_by": None,
        "created_at": int(time.time() * 1000),
    }


def test_get_suggestions_returns_list():
    row = _make_suggestion()
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchall.return_value = [row]
    conn.cursor.return_value = cur

    with patch("api.routes.suggestions.get_monitor_conn", return_value=conn):
        client = TestClient(app)
        resp = client.get("/api/suggestions?status=0")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == 1


def test_approve_threshold_suggestion(tmp_path):
    import json as _json
    cfg = {"recharge": {"success_rate_warning": 0.80, "success_rate_critical": 0.60}}
    (tmp_path / "thresholds_payment.json").write_text(_json.dumps(cfg), encoding="utf-8")

    row = _make_suggestion(id=1, source="threshold_optimizer", item_type="adjust")
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchone.return_value = row
    conn.cursor.return_value = cur

    with patch("api.routes.suggestions.get_monitor_conn", return_value=conn), \
         patch("api.actions.threshold.CONFIG_DIR", tmp_path):
        client = TestClient(app)
        resp = client.post("/api/suggestions/1/approve", json={
            "overrides": {"suggested": {"warning": 0.85}},
            "reviewed_by": "operator"
        })

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_reject_suggestion():
    row = _make_suggestion(id=2)
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchone.return_value = row
    conn.cursor.return_value = cur

    with patch("api.routes.suggestions.get_monitor_conn", return_value=conn):
        client = TestClient(app)
        resp = client.post("/api/suggestions/2/reject", json={"reviewed_by": "operator"})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_approve_unknown_id_returns_404():
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchone.return_value = None
    conn.cursor.return_value = cur

    with patch("api.routes.suggestions.get_monitor_conn", return_value=conn):
        client = TestClient(app)
        resp = client.post("/api/suggestions/999/approve", json={})

    assert resp.status_code == 404
