import json
import time
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config.db import get_monitor_conn
import api.actions.threshold as _threshold_mod
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
            _threshold_mod.apply_threshold(
                metric=row["metric_key"],
                suggested=payload.get("suggested", {}),
                config_dir=_threshold_mod.CONFIG_DIR,
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
                _json.dumps(payload.get("must_phrases", payload.get("suggested_phrases", [])), ensure_ascii=False),
                payload.get("priority"),
                _json.dumps(payload.get("suggested_phrases", []), ensure_ascii=False),
                now,
                "operator",
            ),
        )
    conn.commit()
