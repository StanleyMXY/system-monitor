import argparse
import json
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

from config.db import get_monitor_conn

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent.parent / "output"
ALERT_LOG_PATH = Path(__file__).parent.parent / "logs" / "alerts.log"
_CHAINS_PATH = Path(__file__).parent.parent / "config" / "causal_chains.json"
_MIN_ALERTS = 5


def _load_metric_history() -> list[dict]:
    """从 monitor_metric_history 读取近 24h 的 warning/critical 记录。"""
    cutoff_ms = int((time.time() - 86400) * 1000)
    conn = get_monitor_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT domain, metric, channel_id, value, level, recorded_at
                FROM monitor_metric_history
                WHERE level != 'ok' AND recorded_at >= %s
                ORDER BY recorded_at ASC
                """,
                (cutoff_ms,),
            )
            return list(cur.fetchall())
    finally:
        conn.close()


def _load_alerts_log() -> list[dict]:
    """从 alerts.log 读取近 24h 的告警条目。"""
    cutoff_ts = int(time.time()) - 86400
    entries = []
    if not ALERT_LOG_PATH.exists():
        return entries
    with open(ALERT_LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get("ts", 0) >= cutoff_ts:
                    entries.append(obj)
            except json.JSONDecodeError:
                continue
    return sorted(entries, key=lambda x: x["ts"])


def _load_causal_chains() -> list[dict]:
    if not _CHAINS_PATH.exists():
        return []
    try:
        with open(_CHAINS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return []


def _save_suggested_chains(chains: list[dict]) -> None:
    """将 LLM 建议的因果链追加到 causal_chains.json。"""
    existing = _load_causal_chains()
    existing.extend(chains)
    with open(_CHAINS_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


def _save_report_to_db(analysis_date: date, alert_count: int,
                       chain_count: int, report: dict,
                       suggested_chains: list[dict] | None) -> None:
    conn = get_monitor_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO monitor_root_cause_reports
                  (analysis_date, alert_count, chain_count, report_json,
                   suggested_chains, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  alert_count = VALUES(alert_count),
                  chain_count = VALUES(chain_count),
                  report_json = VALUES(report_json),
                  suggested_chains = VALUES(suggested_chains)
                """,
                (
                    analysis_date.isoformat(),
                    alert_count,
                    chain_count,
                    json.dumps(report, ensure_ascii=False),
                    json.dumps(suggested_chains, ensure_ascii=False) if suggested_chains else None,
                    int(time.time() * 1000),
                ),
            )
        conn.commit()
    finally:
        conn.close()


_SYSTEM_PROMPT = """你是一个运营监控系统的根因分析专家。
你会收到近 24 小时的告警事件序列（含跨域实时关联结果）和当前已知因果链配置。
请分析告警序列，识别因果链，并建议新增的因果链规则。

输出严格为 JSON，格式：
{
  "identified_chains": [
    {
      "cause_domain": "log",
      "cause_metric": "mq_route_error_count",
      "effect_domain": "payment",
      "effect_metric": "recharge_success_rate",
      "confidence": "high|medium|low",
      "evidence": "简短证据描述",
      "lead_minutes": 6.2
    }
  ],
  "suggested_new_chains": [
    {
      "cause": {"domain": "...", "metric": "..."},
      "effects": [{"domain": "...", "metric": "..."}],
      "description": "..."
    }
  ],
  "summary": "一段话总结今日告警根因情况"
}

如果数据不足以得出结论，identified_chains 和 suggested_new_chains 返回空列表，summary 说明原因。
"""


def _run_llm_analysis(history: list[dict], alerts: list[dict],
                      chains: list[dict]) -> dict:
    from langchain.chat_models import init_chat_model

    model_name = os.getenv("LLM_MODEL", "qwen3-max")
    llm = init_chat_model(model_name)

    payload = {
        "alert_count": len(alerts),
        "metric_history_sample": history[:50],
        "alert_log_entries": alerts,
        "known_causal_chains": chains,
    }
    user_msg = f"请分析以下监控数据：\n\n{json.dumps(payload, ensure_ascii=False, indent=2)}"

    response = llm.invoke([
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ])

    raw = response.content.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    return json.loads(raw)


def main(interactive: bool = True) -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    today = date.today()
    cache_path = OUTPUT_DIR / f"root_cause_{today.strftime('%Y%m%d')}.json"

    history = _load_metric_history()
    alerts = _load_alerts_log()

    if len(alerts) < _MIN_ALERTS:
        logger.info(f"告警数不足 {_MIN_ALERTS} 条（当前 {len(alerts)} 条），跳过分析")
        return

    use_cache = "--from-cache" in sys.argv
    if use_cache and cache_path.exists():
        logger.info("使用缓存结果，跳过 LLM 调用")
        with open(cache_path, encoding="utf-8") as f:
            report = json.load(f)
        analysis = report.get("analysis", {})
    else:
        chains = _load_causal_chains()
        logger.info(f"开始 LLM 根因分析（告警 {len(alerts)} 条，历史指标 {len(history)} 条）")
        analysis = _run_llm_analysis(history, alerts, chains)
        report = {
            "analysis_date": today.isoformat(),
            "alert_count": len(alerts),
            "history_count": len(history),
            "analysis": analysis,
        }
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info(f"报告已写入 {cache_path}")

    identified = analysis.get("identified_chains", [])
    suggested = analysis.get("suggested_new_chains", [])
    summary = analysis.get("summary", "")
    chain_count = len(identified)

    _save_report_to_db(today, len(alerts), chain_count, report, suggested if suggested else None)
    logger.info(f"报告已写入 DB（analysis_date={today}）")

    if summary:
        print(f"\n=== 今日根因分析摘要 ===\n{summary}\n")
    if identified:
        print(f"识别出 {chain_count} 条因果链：")
        for ch in identified:
            print(f"  [{ch.get('confidence','?')}] {ch.get('cause_domain')}.{ch.get('cause_metric')} "
                  f"→ {ch.get('effect_domain')}.{ch.get('effect_metric')} "
                  f"(提前 {ch.get('lead_minutes','?')} 分钟)")

    if interactive and suggested:
        print(f"\nLLM 建议新增 {len(suggested)} 条因果链：")
        for i, ch in enumerate(suggested):
            print(f"  [{i+1}] {ch.get('description', json.dumps(ch, ensure_ascii=False))}")
        choice = input("\n[a] 全部采纳并写入 causal_chains.json  [q] 退出: ").strip().lower()
        if choice == "a":
            _save_suggested_chains(suggested)
            logger.info(f"已采纳 {len(suggested)} 条建议因果链")
            print("已写入 config/causal_chains.json")
    elif not interactive and suggested:
        logger.info(f"非交互模式：{len(suggested)} 条建议因果链已存入 DB，待人工采纳")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="根因分析器")
    parser.add_argument("--from-cache", action="store_true", help="跳过 LLM，复用当日缓存")
    args = parser.parse_args()
    main(interactive=True)
