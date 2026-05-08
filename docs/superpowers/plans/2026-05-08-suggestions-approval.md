# Suggestions Approval System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace CLI-only batch approval with a per-item Web approval system backed by DB, with a standalone FastAPI server replacing the embedded SimpleHTTPRequestHandler.

**Architecture:** Three analyzers write suggestions to `monitor_suggestions` DB table after each analysis run; a new `api/server.py` (FastAPI, port 8080) serves `output/` static files and provides REST endpoints for approve/reject; `suggestions_detail.html` becomes an interactive page calling these APIs; `scheduler.py` disables its embedded HTTP server.

**Tech Stack:** FastAPI, uvicorn, pymysql (already in use via `config/db.py`), vanilla JS (existing dashboard style)

---

## File Map

**New files:**
- `db/migrate_suggestions.sql` — `monitor_suggestions` table
- `engine/suggestions_store.py` — `insert_suggestions()`, `load_rejected_context()`
- `api/__init__.py`
- `api/server.py` — FastAPI app entry, uvicorn launch
- `api/routes/__init__.py`
- `api/routes/suggestions.py` — GET/POST routes
- `api/actions/__init__.py`
- `api/actions/threshold.py` — apply threshold change to JSON
- `api/actions/noise.py` — apply noise rule to JSON + DB
- `api/actions/chain.py` — append causal chain to JSON
- `tests/test_suggestions_store.py`
- `tests/test_api_suggestions.py`
- `tests/test_api_actions.py`

**Modified files:**
- `engine/threshold_config.py` — add `METRIC_THRESHOLD_MAP` (moved from optimizer)
- `engine/threshold_optimizer.py` — import map from threshold_config; call insert_suggestions; pass rejected context to LLM
- `engine/log_analyzer.py` — call insert_suggestions; pass rejected context to LLM
- `engine/root_cause_analyzer.py` — call insert_suggestions; pass rejected context to LLM
- `dashboard/monitor_dashboard.py` — `_load_pending_suggestions()` reads DB instead of JSON files
- `scheduler.py` — `MonitorDashboard(port=0)`
- `output/suggestions_detail.html` — interactive approval UI

---

## Task 1: DB Migration

**Files:**
- Create: `db/migrate_suggestions.sql`

- [ ] **Step 1: Write migration SQL**

```sql
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
```

Save to `db/migrate_suggestions.sql`.

- [ ] **Step 2: Commit**

```bash
git add db/migrate_suggestions.sql
git commit -m "feat: add monitor_suggestions migration"
```

---

## Task 2: `engine/suggestions_store.py`

**Files:**
- Create: `engine/suggestions_store.py`
- Create: `tests/test_suggestions_store.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_suggestions_store.py
import time
import pytest
from unittest.mock import patch, MagicMock
from engine.suggestions_store import insert_suggestions, load_rejected_context


def _mock_conn(fetchall_return=None):
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    if fetchall_return is not None:
        cur.fetchall.return_value = fetchall_return
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def test_insert_suggestions_calls_execute():
    conn, cur = _mock_conn()
    rows = [
        {
            "source": "threshold_optimizer",
            "analysis_date": "2026-05-08",
            "item_type": "adjust",
            "metric_key": "recharge_success_rate",
            "payload": {"metric": "recharge_success_rate"},
        }
    ]
    with patch("engine.suggestions_store.get_monitor_conn", return_value=conn):
        insert_suggestions(rows)
    assert cur.execute.called


def test_insert_suggestions_empty_is_noop():
    conn, cur = _mock_conn()
    with patch("engine.suggestions_store.get_monitor_conn", return_value=conn):
        insert_suggestions([])
    conn.cursor.assert_not_called()


def test_load_rejected_context_returns_list():
    now_ms = int(time.time() * 1000)
    row = {
        "source": "threshold_optimizer",
        "item_type": "adjust",
        "metric_key": "recharge_success_rate",
        "payload": '{"metric": "recharge_success_rate"}',
        "reviewed_at": now_ms,
    }
    conn, cur = _mock_conn(fetchall_return=[row])
    with patch("engine.suggestions_store.get_monitor_conn", return_value=conn):
        result = load_rejected_context("threshold_optimizer", 30)
    assert len(result) == 1
    assert result[0]["metric_key"] == "recharge_success_rate"
    assert isinstance(result[0]["payload"], dict)
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/test_suggestions_store.py -v
```

Expected: ImportError or similar (module doesn't exist yet).

- [ ] **Step 3: Write `engine/suggestions_store.py`**

```python
import json
import time
from config.db import get_monitor_conn


def insert_suggestions(rows: list[dict]) -> None:
    if not rows:
        return
    now = int(time.time() * 1000)
    conn = get_monitor_conn()
    try:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(
                    """
                    INSERT IGNORE INTO monitor_suggestions
                      (source, analysis_date, item_type, metric_key, payload, status, created_at)
                    VALUES (%s, %s, %s, %s, %s, 0, %s)
                    """,
                    (
                        row["source"],
                        row["analysis_date"],
                        row["item_type"],
                        row.get("metric_key"),
                        json.dumps(row["payload"], ensure_ascii=False),
                        now,
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def load_rejected_context(source: str, window_days: int) -> list[dict]:
    cutoff = int(time.time() * 1000) - window_days * 86400 * 1000
    conn = get_monitor_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source, item_type, metric_key, payload, reviewed_at
                FROM monitor_suggestions
                WHERE source = %s AND status = 2 AND reviewed_at >= %s
                ORDER BY reviewed_at DESC
                """,
                (source, cutoff),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    result = []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        result.append({
            "source": r["source"],
            "item_type": r["item_type"],
            "metric_key": r["metric_key"],
            "payload": payload,
            "reviewed_at": r["reviewed_at"],
        })
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```
python -m pytest tests/test_suggestions_store.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add engine/suggestions_store.py tests/test_suggestions_store.py
git commit -m "feat: add suggestions_store insert and rejected context loader"
```

---

## Task 3: Move `_METRIC_THRESHOLD_MAP` to `engine/threshold_config.py`

**Files:**
- Modify: `engine/threshold_config.py`
- Modify: `engine/threshold_optimizer.py`
- Test: `tests/test_threshold_config.py`

- [ ] **Step 1: Add the map to `engine/threshold_config.py`**

Append after the existing `load_thresholds` function:

```python
METRIC_THRESHOLD_MAP: dict[str, tuple[str, str, dict]] = {
    # payment
    "recharge_success_rate": ("payment", "recharge", {"warning": "success_rate_warning", "critical": "success_rate_critical"}),
    "recharge_timeout_rate": ("payment", "recharge", {"warning": "timeout_rate_warning"}),
    "recharge_pending_count": ("payment", "recharge", {"warning": "pending_count_warning"}),
    "channel_balance": ("payment", "channel_account", {"warning": "balance_warning_amount"}),
    "withdraw_queue_count": ("payment", "withdraw", {"warning": "queue_count_warning"}),
    "withdraw_fail_rate": ("payment", "withdraw", {"warning": "fail_rate_warning"}),
    # game
    "game_transfer_fail_rate": ("game", "balance_transfer", {"warning": "fail_rate_warning"}),
    "game_transfer_retry_count": ("game", "balance_transfer", {"warning": "retry_order_count_warning"}),
    "game_reconciliation_diff_days": ("game", "reconciliation", {"critical": "diff_consecutive_days_critical"}),
    # risk
    "risk_alert_backlog_count": ("risk", "alert", {"warning": "high_risk_pending_warning"}),
    "risk_alert_timeout_count": ("risk", "alert", {}),
    "risk_event_backlog_count": ("risk", "event", {"warning": "high_risk_pending_warning"}),
    "risk_blacklist_expiry_count": ("risk", "blacklist", {}),
    # activity
    "redemption_fail_rate": ("activity", "redemption", {"warning": "fail_rate_warning"}),
    "first_deposit_fail_count": ("activity", "first_deposit", {"warning": "fail_alert_threshold"}),
    # account
    "frozen_balance_growth_rate": ("account", "frozen_balance", {"warning": "growth_rate_warning"}),
    # operation
    "vip_adjust_count": ("operation", "vip_adjust", {"warning": "batch_count_per_hour_warning"}),
    "balance_adjustment_large_count": ("operation", "balance_adjustment", {}),
    "config_change_count": ("operation", "config_change", {"warning": "change_count_per_hour_warning"}),
}
```

- [ ] **Step 2: Update `engine/threshold_optimizer.py` to import from threshold_config**

Replace the `_METRIC_THRESHOLD_MAP = { ... }` block (lines 93–119) with:

```python
from engine.threshold_config import METRIC_THRESHOLD_MAP as _METRIC_THRESHOLD_MAP
```

- [ ] **Step 3: Write failing test**

```python
# Add to tests/test_threshold_config.py
from engine.threshold_config import METRIC_THRESHOLD_MAP

def test_metric_threshold_map_has_payment_metrics():
    assert "recharge_success_rate" in METRIC_THRESHOLD_MAP
    domain, sub_key, key_map = METRIC_THRESHOLD_MAP["recharge_success_rate"]
    assert domain == "payment"
    assert sub_key == "recharge"
    assert "warning" in key_map

def test_metric_threshold_map_covers_all_domains():
    domains = {v[0] for v in METRIC_THRESHOLD_MAP.values()}
    assert domains == {"payment", "game", "risk", "activity", "account", "operation"}
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/test_threshold_config.py tests/test_threshold_optimizer.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add engine/threshold_config.py engine/threshold_optimizer.py tests/test_threshold_config.py
git commit -m "refactor: move METRIC_THRESHOLD_MAP to threshold_config"
```

---

## Task 4: API Action Handlers

**Files:**
- Create: `api/__init__.py`
- Create: `api/actions/__init__.py`
- Create: `api/actions/threshold.py`
- Create: `api/actions/noise.py`
- Create: `api/actions/chain.py`
- Create: `tests/test_api_actions.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_api_actions.py
import json
import pytest
from pathlib import Path


def test_apply_threshold_writes_json(tmp_path):
    from api.actions.threshold import apply_threshold

    cfg = {
        "_comment": "test",
        "recharge": {
            "success_rate_warning": 0.80,
            "success_rate_critical": 0.60,
        }
    }
    cfg_file = tmp_path / "thresholds_payment.json"
    cfg_file.write_text(json.dumps(cfg), encoding="utf-8")

    apply_threshold(
        metric="recharge_success_rate",
        suggested={"warning": 0.85, "critical": 0.65},
        config_dir=tmp_path,
    )

    result = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert result["recharge"]["success_rate_warning"] == 0.85
    assert result["recharge"]["success_rate_critical"] == 0.65


def test_apply_threshold_unknown_metric_raises(tmp_path):
    from api.actions.threshold import apply_threshold
    with pytest.raises(ValueError, match="unknown metric"):
        apply_threshold(metric="nonexistent_metric", suggested={"warning": 1.0}, config_dir=tmp_path)


def test_append_noise_rule_creates_file(tmp_path):
    from api.actions.noise import append_noise_rule

    noise_path = tmp_path / "noise_rules.json"
    append_noise_rule(
        term="risk_task_fail_Delay",
        description="RabbitMQ 延迟队列失败",
        suggested_phrases=["risk_task_fail", "Delay"],
        noise_path=noise_path,
    )

    data = json.loads(noise_path.read_text(encoding="utf-8"))
    assert len(data["rules"]) == 1
    assert data["rules"][0]["must_phrases"] == ["risk_task_fail", "Delay"]


def test_append_noise_rule_increments_id(tmp_path):
    from api.actions.noise import append_noise_rule

    noise_path = tmp_path / "noise_rules.json"
    append_noise_rule("term1", "desc1", ["phrase1"], noise_path)
    append_noise_rule("term2", "desc2", ["phrase2"], noise_path)

    data = json.loads(noise_path.read_text(encoding="utf-8"))
    ids = [r["id"] for r in data["rules"]]
    assert ids == ["noise_001", "noise_002"]


def test_append_causal_chain_creates_file(tmp_path):
    from api.actions.chain import append_causal_chain

    chains_path = tmp_path / "causal_chains.json"
    chain = {"cause_domain": "payment", "cause_metric": "recharge_success_rate",
             "effect_domain": "game", "effect_metric": "game_transfer_fail_rate",
             "description": "test chain"}
    append_causal_chain(chain, chains_path)

    data = json.loads(chains_path.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["cause_domain"] == "payment"
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/test_api_actions.py -v
```

Expected: ImportError.

- [ ] **Step 3: Create `api/__init__.py` and `api/actions/__init__.py`**

Both empty files.

- [ ] **Step 4: Write `api/actions/threshold.py`**

```python
import json
from pathlib import Path
from engine.threshold_config import METRIC_THRESHOLD_MAP

CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


def apply_threshold(metric: str, suggested: dict, config_dir: Path = CONFIG_DIR) -> None:
    if metric not in METRIC_THRESHOLD_MAP:
        raise ValueError(f"unknown metric: {metric}")

    domain, sub_key, key_map = METRIC_THRESHOLD_MAP[metric]
    path = config_dir / f"thresholds_{domain}.json"

    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)

    for role, json_key in key_map.items():
        value = suggested.get(role)
        if value is not None:
            cfg.setdefault(sub_key, {})[json_key] = value

    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
```

- [ ] **Step 5: Write `api/actions/noise.py`**

```python
import json
import re
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

_NOISE_RULES_PATH = Path(__file__).parent.parent.parent / "config" / "noise_rules.json"


def append_noise_rule(
    term: str,
    description: str,
    suggested_phrases: list[str],
    noise_path: Path = _NOISE_RULES_PATH,
) -> None:
    if noise_path.exists():
        with open(noise_path, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"_comment": "已确认的日志噪音规则", "rules": []}

    existing_ids = [r.get("id", "") for r in data.get("rules", [])]
    used_nums = [int(m.group(1)) for rid in existing_ids if (m := re.match(r"noise_(\d+)$", rid))]
    next_num = max(used_nums, default=0) + 1

    data.setdefault("rules", []).append({
        "id": f"noise_{next_num:03d}",
        "description": description,
        "must_phrases": suggested_phrases,
        "confirmed_at": datetime.now().strftime("%Y-%m-%d"),
        "confirmed_by": "operator",
    })

    with open(noise_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
```

- [ ] **Step 6: Write `api/actions/chain.py`**

```python
import json
from pathlib import Path

_CHAINS_PATH = Path(__file__).parent.parent.parent / "config" / "causal_chains.json"


def append_causal_chain(chain: dict, chains_path: Path = _CHAINS_PATH) -> None:
    if chains_path.exists():
        with open(chains_path, encoding="utf-8") as f:
            existing = json.load(f)
    else:
        existing = []

    existing.append(chain)

    with open(chains_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
```

- [ ] **Step 7: Run tests to verify they pass**

```
python -m pytest tests/test_api_actions.py -v
```

Expected: 6 passed.

- [ ] **Step 8: Commit**

```bash
git add api/ tests/test_api_actions.py
git commit -m "feat: add api action handlers for threshold, noise, chain"
```

---

## Task 5: FastAPI Server

**Files:**
- Create: `api/routes/__init__.py`
- Create: `api/routes/suggestions.py`
- Modify: `api/server.py`
- Create: `tests/test_api_suggestions.py`

- [ ] **Step 1: Install fastapi and uvicorn if not present**

```
pip install fastapi uvicorn
```

Add to `requirements.txt` if it exists. Check:
```
pip show fastapi uvicorn
```

- [ ] **Step 2: Write failing tests**

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

```
python -m pytest tests/test_api_suggestions.py -v
```

Expected: ImportError.

- [ ] **Step 4: Create `api/routes/__init__.py`** (empty file)

- [ ] **Step 5: Write `api/routes/suggestions.py`**

```python
import json
import time
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config.db import get_monitor_conn
from api.actions.threshold import apply_threshold
from api.actions.noise import append_noise_rule
from api.actions.chain import append_causal_chain

router = APIRouter()


class ApproveRequest(BaseModel):
    overrides: dict = {}
    reviewed_by: str = "operator"


class RejectRequest(BaseModel):
    reviewed_by: str = "operator"


def _fetch_suggestion(conn, suggestion_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM monitor_suggestions WHERE id = %s",
            (suggestion_id,),
        )
        return cur.fetchone()


def _mark_reviewed(conn, suggestion_id: int, status: int, reviewed_by: str) -> None:
    now = int(time.time() * 1000)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE monitor_suggestions SET status=%s, reviewed_at=%s, reviewed_by=%s WHERE id=%s",
            (status, now, reviewed_by, suggestion_id),
        )
    conn.commit()


def _parse_payload(row: dict) -> dict:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return payload


@router.get("/api/suggestions")
def list_suggestions(status: int | None = None, source: str | None = None):
    conn = get_monitor_conn()
    try:
        conditions = []
        params = []
        if status is not None:
            conditions.append("status = %s")
            params.append(status)
        if source:
            conditions.append("source = %s")
            params.append(source)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM monitor_suggestions {where} ORDER BY created_at DESC",
                params,
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    result = []
    for r in rows:
        r = dict(r)
        r["payload"] = _parse_payload(r)
        result.append(r)
    return result


@router.post("/api/suggestions/{suggestion_id}/approve")
def approve_suggestion(suggestion_id: int, body: ApproveRequest):
    conn = get_monitor_conn()
    try:
        row = _fetch_suggestion(conn, suggestion_id)
        if row is None:
            raise HTTPException(status_code=404, detail="suggestion not found")

        payload = _parse_payload(row)
        if body.overrides:
            payload = {**payload, **body.overrides}

        source = row["source"]
        item_type = row["item_type"]

        if source == "threshold_optimizer" and item_type == "adjust":
            apply_threshold(
                metric=row["metric_key"],
                suggested=payload.get("suggested", {}),
            )
        elif source == "log_analyzer" and item_type == "noise":
            append_noise_rule(
                term=row["metric_key"],
                description=payload.get("description", ""),
                suggested_phrases=payload.get("suggested_phrases", []),
            )
            _write_noise_rule_to_db(conn, row, payload)
        elif source == "log_analyzer" and item_type == "new_rule":
            _write_noise_rule_to_db(conn, row, payload)
        elif source == "root_cause_analyzer" and item_type == "new_chain":
            append_causal_chain(payload)

        _mark_reviewed(conn, suggestion_id, status=1, reviewed_by=body.reviewed_by)
    finally:
        conn.close()

    return {"ok": True}


@router.post("/api/suggestions/{suggestion_id}/reject")
def reject_suggestion(suggestion_id: int, body: RejectRequest):
    conn = get_monitor_conn()
    try:
        row = _fetch_suggestion(conn, suggestion_id)
        if row is None:
            raise HTTPException(status_code=404, detail="suggestion not found")
        _mark_reviewed(conn, suggestion_id, status=2, reviewed_by=body.reviewed_by)
    finally:
        conn.close()
    return {"ok": True}


def _write_noise_rule_to_db(conn, row: dict, payload: dict) -> None:
    import json as _json
    now = int(time.time() * 1000)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO monitor_noise_rules
              (rule_type, description, must_phrases, priority, suggested_phrases, confirmed_at, confirmed_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                row["item_type"],
                payload.get("description", ""),
                _json.dumps(payload.get("suggested_phrases", []), ensure_ascii=False),
                payload.get("priority"),
                _json.dumps(payload.get("suggested_phrases", []), ensure_ascii=False),
                now,
                "operator",
            ),
        )
    conn.commit()
```

- [ ] **Step 6: Write `api/server.py`**

```python
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from api.routes.suggestions import router

OUTPUT_DIR = Path(__file__).parent.parent / "output"

app = FastAPI(title="Monitor API")
app.include_router(router)


@app.get("/")
def index():
    return RedirectResponse(url="/monitor_dashboard.html")


@app.get("/{filename}")
def static_file(filename: str):
    path = OUTPUT_DIR / filename
    if not path.exists() or not path.is_file():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path)


if __name__ == "__main__":
    uvicorn.run("api.server:app", host="0.0.0.0", port=8080, reload=False)
```

- [ ] **Step 7: Run tests to verify they pass**

```
python -m pytest tests/test_api_suggestions.py -v
```

Expected: 4 passed.

- [ ] **Step 8: Commit**

```bash
git add api/routes/ api/server.py tests/test_api_suggestions.py
git commit -m "feat: add FastAPI server with suggestions approve/reject endpoints"
```

---

## Task 6: Wire Analyzers to DB

**Files:**
- Modify: `engine/threshold_optimizer.py`
- Modify: `engine/log_analyzer.py`
- Modify: `engine/root_cause_analyzer.py`

- [ ] **Step 1: Update `engine/threshold_optimizer.py`**

In `main()`, after `save_cache(domain_reports, cross_result)` and before the interactive/non-interactive branch, add:

```python
    # Insert suggestions to DB
    from datetime import date as _date
    from engine.suggestions_store import insert_suggestions, load_rejected_context
    today_str = _date.today().isoformat()
    rows = []
    for report in domain_reports:
        for rec in report.get("recommendations", []):
            if rec.get("action") == "adjust":
                rows.append({
                    "source": "threshold_optimizer",
                    "analysis_date": today_str,
                    "item_type": "adjust",
                    "metric_key": rec["metric"],
                    "payload": rec,
                })
    insert_suggestions(rows)
```

In `_call_domain_llm()`, before building `system_prompt`, add rejected context loading. Add a parameter `rejected: list[dict] = None` and append to system_prompt:

```python
    rejected_section = ""
    if rejected:
        lines = "\n".join(f"- [{r['metric_key']}]（已拒绝）" for r in rejected)
        rejected_section = f"\n\n以下建议已被人工拒绝，请勿重复建议：\n{lines}"
```

Then append `{rejected_section}` to the system_prompt string.

In `run_llm_analysis()`, load rejected before calling `_call_domain_llm`:

```python
    from engine.suggestions_store import load_rejected_context
    rejected = load_rejected_context("threshold_optimizer", 30)
```

Pass `rejected=rejected` to each `_call_domain_llm` call.

In `main()`, change the non-interactive branch:

```python
    if not interactive:
        logger.info("[optimizer] 非交互模式，建议已写入 DB，请通过 Web 审批")
        return

    run_interactive_cli(domain_reports, cross_result)
```

- [ ] **Step 2: Update `engine/log_analyzer.py`**

In `main()`, after `save_cache(candidates, patterns)`, before the non-interactive return, add:

```python
    from datetime import date as _date
    from engine.suggestions_store import insert_suggestions
    today_str = _date.today().isoformat()
    samples_by_term = {c["term"]: c.get("samples", []) for c in candidates}
    rows = []
    for p in patterns:
        if p.get("type") in ("new_rule", "noise"):
            rows.append({
                "source": "log_analyzer",
                "analysis_date": today_str,
                "item_type": p["type"],
                "metric_key": p.get("term", "")[:128],
                "payload": {**p, "samples": samples_by_term.get(p.get("term", ""), [])},
            })
    insert_suggestions(rows)
```

Change the non-interactive return:

```python
    if not interactive:
        logger.info(f"[log_analyzer] 非交互模式完成，发现 {len(patterns)} 个模式，建议已写入 DB")
        return
```

In `analyze_with_llm()`, load rejected and append to system_prompt before the LLM call:

```python
    from engine.suggestions_store import load_rejected_context
    rejected = load_rejected_context("log_analyzer", 14)
    if rejected:
        lines = "\n".join(f"- [{r['metric_key'][:60]}]（已拒绝）" for r in rejected)
        system_prompt += f"\n\n以下模式已被人工拒绝，请勿重复建议：\n{lines}"
```

- [ ] **Step 3: Update `engine/root_cause_analyzer.py`**

In `main()`, after `_save_report_to_db(...)`, add:

```python
    from engine.suggestions_store import insert_suggestions
    if suggested:
        chain_rows = []
        for ch in suggested:
            cause = ch.get("cause", {})
            effect_list = ch.get("effects", [ch])
            first_effect = effect_list[0] if effect_list else {}
            metric_key = (
                f"{cause.get('domain','')}.{cause.get('metric','')}→"
                f"{first_effect.get('domain','')}.{first_effect.get('metric','')}"
            )[:128]
            chain_rows.append({
                "source": "root_cause_analyzer",
                "analysis_date": today.isoformat(),
                "item_type": "new_chain",
                "metric_key": metric_key,
                "payload": ch,
            })
        insert_suggestions(chain_rows)
```

In `_run_llm_analysis()`, load rejected and append to `_SYSTEM_PROMPT` when calling:

```python
    from engine.suggestions_store import load_rejected_context
    rejected = load_rejected_context("root_cause_analyzer", 30)
    system = _SYSTEM_PROMPT
    if rejected:
        lines = "\n".join(f"- [{r['metric_key']}]（已拒绝）" for r in rejected)
        system += f"\n\n以下因果链建议已被人工拒绝，请勿重复：\n{lines}"
```

Change `interactive and suggested` branch in `main()`:

```python
    if interactive and suggested:
        print(f"\nLLM 建议新增 {len(suggested)} 条因果链，已写入 DB，可通过 Web 审批或运行 --from-cache 在 CLI 采纳")
```

- [ ] **Step 4: Run existing tests to verify no regressions**

```
python -m pytest tests/test_threshold_optimizer.py tests/test_log_analyzer.py tests/test_root_cause_analyzer.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add engine/threshold_optimizer.py engine/log_analyzer.py engine/root_cause_analyzer.py
git commit -m "feat: wire analyzers to write suggestions to DB with rejected context"
```

---

## Task 7: Dashboard Reads DB for Suggestion Counts

**Files:**
- Modify: `dashboard/monitor_dashboard.py`
- Modify: `scheduler.py`

- [ ] **Step 1: Replace `_load_pending_suggestions` in `dashboard/monitor_dashboard.py`**

Replace the entire `_load_pending_suggestions` method with:

```python
    def _load_pending_suggestions(self) -> dict:
        result = {"threshold": 0, "chains": 0, "log_patterns": 0, "date": "--"}
        try:
            from config.db import get_monitor_conn
            conn = get_monitor_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT source, COUNT(*) AS cnt
                        FROM monitor_suggestions
                        WHERE status = 0
                        GROUP BY source
                        """,
                    )
                    rows = cur.fetchall()
            finally:
                conn.close()
            for r in rows:
                if r["source"] == "threshold_optimizer":
                    result["threshold"] = r["cnt"]
                elif r["source"] == "root_cause_analyzer":
                    result["chains"] = r["cnt"]
                elif r["source"] == "log_analyzer":
                    result["log_patterns"] = r["cnt"]
        except Exception:
            pass
        return result
```

Also update `_render` to use `result["threshold"]`, `result["chains"]`, `result["log_patterns"]` as counts (they are now ints, not lists). Replace:

```python
        threshold_count = len(suggestions["threshold"])
        chain_count = len(suggestions["chains"])
        log_count = len(suggestions["log_patterns"])
```

With:

```python
        threshold_count = suggestions["threshold"]
        chain_count = suggestions["chains"]
        log_count = suggestions["log_patterns"]
```

- [ ] **Step 2: Update `scheduler.py`** — change port from 8080 to 0

```python
dashboard = MonitorDashboard(port=0)
```

- [ ] **Step 3: Run dashboard tests**

```
python -m pytest tests/test_monitor_dashboard.py -v
```

Expected: all pass (the tests use `port=0` and mock `_html_path`, so DB calls are the only new code path — the `except Exception: pass` handles the no-DB test environment).

- [ ] **Step 4: Commit**

```bash
git add dashboard/monitor_dashboard.py scheduler.py
git commit -m "feat: dashboard reads suggestion counts from DB, disable embedded HTTP server"
```

---

## Task 8: Interactive Approval UI

**Files:**
- Modify: `output/suggestions_detail.html`

- [ ] **Step 1: Rewrite `output/suggestions_detail.html`**

Replace the entire file with:

```html
<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <title>待审批建议 — 天工平台监控</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #1a1a2e; color: #ccc; font-family: 'Courier New', monospace; padding: 24px; }
    .header { margin-bottom: 24px; }
    .header h1 { color: #4a9eff; font-size: 16px; margin-bottom: 4px; }
    .header .meta { color: #555; font-size: 12px; }
    .badge { display: inline-block; background: #4a9eff22; color: #4a9eff; border-radius: 4px; padding: 2px 8px; font-size: 12px; margin-left: 8px; }
    .section { margin-bottom: 32px; }
    .section-title { color: #aaa; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px; padding-bottom: 4px; border-bottom: 1px solid #0f3460; }
    .card { background: #0f3460; border-radius: 8px; padding: 16px; margin-bottom: 10px; }
    .card-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 10px; }
    .metric-key { color: #4a9eff; font-size: 13px; font-weight: bold; }
    .item-type { color: #555; font-size: 11px; margin-left: 8px; }
    .field { margin-bottom: 6px; font-size: 12px; }
    .field label { color: #777; margin-right: 8px; min-width: 60px; display: inline-block; }
    .field input[type=number] { background: #1a1a2e; border: 1px solid #4a9eff44; color: #ccc; padding: 3px 8px; border-radius: 4px; font-family: monospace; width: 100px; }
    .field textarea { background: #1a1a2e; border: 1px solid #4a9eff44; color: #ccc; padding: 6px 8px; border-radius: 4px; font-family: monospace; width: 100%; height: 60px; resize: vertical; font-size: 12px; }
    .samples { margin-top: 8px; }
    .sample { color: #555; font-size: 11px; padding: 3px 0; border-bottom: 1px solid #1a1a2e22; }
    .actions { display: flex; gap: 8px; margin-top: 12px; }
    .btn { padding: 6px 16px; border-radius: 4px; border: none; cursor: pointer; font-size: 12px; font-family: monospace; }
    .btn-approve { background: #27ae6022; color: #27ae60; border: 1px solid #27ae6044; }
    .btn-approve:hover { background: #27ae6033; }
    .btn-reject { background: #e9456022; color: #e94560; border: 1px solid #e9456044; }
    .btn-reject:hover { background: #e9456033; }
    .btn:disabled { opacity: 0.4; cursor: not-allowed; }
    .empty { color: #555; font-size: 12px; padding: 8px 0; }
    a { color: #555; text-decoration: none; }
    a:hover { color: #4a9eff; }
  </style>
</head>
<body>
  <div class="header">
    <h1>待审批建议 <span class="badge" id="total-badge">加载中...</span></h1>
    <div class="meta"><a href="monitor_dashboard.html">← 返回监控看板</a></div>
  </div>

  <div class="section">
    <div class="section-title">阈值调整</div>
    <div id="section-threshold"><span class="empty">加载中...</span></div>
  </div>

  <div class="section">
    <div class="section-title">新因果链</div>
    <div id="section-chain"><span class="empty">加载中...</span></div>
  </div>

  <div class="section">
    <div class="section-title">日志模式</div>
    <div id="section-log"><span class="empty">加载中...</span></div>
  </div>

<script>
let allItems = [];

async function load() {
  try {
    const resp = await fetch('/api/suggestions?status=0');
    allItems = await resp.json();
    render();
  } catch(e) {
    document.getElementById('total-badge').textContent = '加载失败';
  }
}

function updateBadge() {
  document.getElementById('total-badge').textContent = allItems.length + ' 条';
}

function render() {
  updateBadge();
  const threshold = allItems.filter(i => i.source === 'threshold_optimizer');
  const chains = allItems.filter(i => i.source === 'root_cause_analyzer');
  const logs = allItems.filter(i => i.source === 'log_analyzer');

  document.getElementById('section-threshold').innerHTML =
    threshold.length ? threshold.map(renderThreshold).join('') : '<span class="empty">暂无</span>';
  document.getElementById('section-chain').innerHTML =
    chains.length ? chains.map(renderChain).join('') : '<span class="empty">暂无</span>';
  document.getElementById('section-log').innerHTML =
    logs.length ? logs.map(renderLog).join('') : '<span class="empty">暂无</span>';
}

function renderThreshold(item) {
  const p = item.payload;
  const cur = p.current || {};
  const sug = p.suggested || {};
  const warnId = `warn-${item.id}`;
  const critId = `crit-${item.id}`;
  return `<div class="card" id="card-${item.id}">
    <div class="card-header">
      <span><span class="metric-key">${item.metric_key}</span><span class="item-type">[${p.domain || ''}]</span></span>
    </div>
    <div class="field"><label>当前阈值</label>warning=${cur.warning ?? '—'} &nbsp; critical=${cur.critical ?? '—'}</div>
    <div class="field"><label>建议阈值</label>
      warning <input type="number" id="${warnId}" value="${sug.warning ?? ''}" step="0.01" min="0" max="1">
      &nbsp; critical <input type="number" id="${critId}" value="${sug.critical ?? ''}" step="0.01" min="0" max="1">
    </div>
    <div class="field"><label>理由</label>${p.reason || '—'}</div>
    <div class="actions">
      <button class="btn btn-approve" onclick="approveThreshold(${item.id}, '${warnId}', '${critId}')">采纳</button>
      <button class="btn btn-reject" onclick="doReject(${item.id})">拒绝</button>
    </div>
  </div>`;
}

function renderChain(item) {
  const p = item.payload;
  const cause = p.cause || {};
  const effects = p.effects || [];
  const effStr = effects.map(e => `${e.domain || ''}.${e.metric || ''}`).join(', ');
  return `<div class="card" id="card-${item.id}">
    <div class="metric-key">${cause.domain || ''}.${cause.metric || ''} → ${effStr}</div>
    <div class="field" style="margin-top:8px"><label>描述</label>${p.description || '—'}</div>
    <div class="actions">
      <button class="btn btn-approve" onclick="doApprove(${item.id}, {})">采纳</button>
      <button class="btn btn-reject" onclick="doReject(${item.id})">拒绝</button>
    </div>
  </div>`;
}

function renderLog(item) {
  const p = item.payload;
  const phrasesId = `phrases-${item.id}`;
  const samples = (p.samples || []).slice(0, 3);
  const label = item.item_type === 'noise' ? '确认为噪音' : '记录为待建规则';
  return `<div class="card" id="card-${item.id}">
    <div class="card-header">
      <span><span class="metric-key">${(item.metric_key || '').slice(0, 80)}</span><span class="item-type">[${item.item_type}] [${p.priority || ''}]</span></span>
    </div>
    <div class="field"><label>描述</label>${p.description || '—'}</div>
    ${p.root_cause ? `<div class="field"><label>根因</label>${p.root_cause}</div>` : ''}
    <div class="field"><label>过滤词</label>
      <textarea id="${phrasesId}">${(p.suggested_phrases || []).join(', ')}</textarea>
    </div>
    ${samples.length ? `<div class="samples">${samples.map(s => `<div class="sample">${s.slice(0,120)}</div>`).join('')}</div>` : ''}
    <div class="actions">
      <button class="btn btn-approve" onclick="approveLog(${item.id}, '${phrasesId}')">${label}</button>
      <button class="btn btn-reject" onclick="doReject(${item.id})">拒绝</button>
    </div>
  </div>`;
}

async function approveThreshold(id, warnId, critId) {
  const warnEl = document.getElementById(warnId);
  const critEl = document.getElementById(critId);
  const overrides = { suggested: {} };
  if (warnEl && warnEl.value !== '') overrides.suggested.warning = parseFloat(warnEl.value);
  if (critEl && critEl.value !== '') overrides.suggested.critical = parseFloat(critEl.value);
  await doApprove(id, overrides);
}

async function approveLog(id, phrasesId) {
  const el = document.getElementById(phrasesId);
  const phrases = el ? el.value.split(',').map(s => s.trim()).filter(Boolean) : [];
  await doApprove(id, { suggested_phrases: phrases });
}

async function doApprove(id, overrides) {
  setCardLoading(id, true);
  try {
    const resp = await fetch(`/api/suggestions/${id}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ overrides, reviewed_by: 'operator' }),
    });
    if (!resp.ok) { alert('操作失败: ' + (await resp.text())); setCardLoading(id, false); return; }
    removeCard(id);
  } catch(e) { alert('网络错误'); setCardLoading(id, false); }
}

async function doReject(id) {
  setCardLoading(id, true);
  try {
    const resp = await fetch(`/api/suggestions/${id}/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reviewed_by: 'operator' }),
    });
    if (!resp.ok) { alert('操作失败: ' + (await resp.text())); setCardLoading(id, false); return; }
    removeCard(id);
  } catch(e) { alert('网络错误'); setCardLoading(id, false); }
}

function setCardLoading(id, loading) {
  const card = document.getElementById(`card-${id}`);
  if (!card) return;
  card.querySelectorAll('.btn').forEach(b => b.disabled = loading);
}

function removeCard(id) {
  allItems = allItems.filter(i => i.id !== id);
  render();
}

load();
</script>
</body>
</html>
```

- [ ] **Step 2: Verify the server can serve the file**

Start the API server in one terminal, then open `http://localhost:8080/suggestions_detail.html` in the browser and confirm it loads without JS errors.

```
python -m api.server
```

- [ ] **Step 3: Commit**

```bash
git add output/suggestions_detail.html
git commit -m "feat: rewrite suggestions_detail.html as interactive approval UI"
```

---

## Task 9: Run Full Test Suite and Final Verification

- [ ] **Step 1: Run full test suite**

```
python -m pytest tests/ -v --tb=short
```

Expected: all existing tests pass, new tests pass.

- [ ] **Step 2: Apply DB migration**

```
mysql -u <MONITOR_USER> -p tg_monitor < db/migrate_suggestions.sql
```

- [ ] **Step 3: Smoke test the API server**

Start server:
```
python -m api.server
```

In another shell:
```
curl http://localhost:8080/api/suggestions?status=0
curl http://localhost:8080/monitor_dashboard.html -I
```

Expected: `200 OK` for both.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: complete suggestions approval system refactor"
```
