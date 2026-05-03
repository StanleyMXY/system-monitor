import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from config.db import get_monitor_conn

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent.parent / "output"
_NOISE_RULES_PATH = Path(__file__).parent.parent / "config" / "noise_rules.json"

_EXISTING_RULES_SUMMARY = """现有监控规则（已覆盖，请勿重复建议）：
1. 游戏启动失败：LaunchController + IOException
2. MQ 路由失败：PRECONDITION_FAILED + Cannot route message
3. 分表路由失败：ShardingSphereException
4. WebSocket 严重异常：SocketServer + 严重异常
5. DB 唯一键冲突：SQLIntegrityConstraintViolation
"""


def _load_noise_rules() -> list[dict]:
    """加载噪音规则列表（原始规则对象）。"""
    if not _NOISE_RULES_PATH.exists():
        return []
    with open(_NOISE_RULES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("rules", [])


def _candidate_matches_noise(term: str, noise_rules: list[dict]) -> bool:
    """检查候选词条是否命中任一噪音规则（所有 must_phrases 都出现在 term 中）。"""
    for rule in noise_rules:
        phrases = rule.get("must_phrases", [])
        if phrases and all(p.lower() in term.lower() for p in phrases):
            return True
    return False


def fetch_candidates(es, prefix: str) -> list[dict]:
    """
    对 ES 执行 significant_terms 聚合，返回经噪音预过滤的候选模式列表。
    每个候选：{"term": str, "doc_count": int, "score": float, "samples": list[str]}
    """
    index = f"{prefix}*"
    agg_body = {
        "query": {
            "bool": {
                "must": [
                    {"range": {"@timestamp": {"gte": "now-24h"}}},
                    {"bool": {"should": [
                        {"match": {"message": "WARN"}},
                        {"match": {"message": "ERROR"}},
                        {"match": {"message": "Exception"}},
                    ], "minimum_should_match": 1}},
                ]
            }
        },
        "aggs": {
            "new_patterns": {
                "significant_terms": {
                    "field": "message",
                    "size": 20,
                    "background_filter": {
                        "range": {"@timestamp": {"gte": "now-7d"}}
                    },
                    "min_doc_count": 5,
                }
            }
        },
        "size": 0,
    }

    resp = es.search(index=index, body=agg_body)
    buckets = resp.get("aggregations", {}).get("new_patterns", {}).get("buckets", [])

    noise_rules = _load_noise_rules()
    candidates = []

    for bucket in buckets:
        term = bucket["key"]
        if _candidate_matches_noise(term, noise_rules):
            logger.info(f"[log_analyzer] 噪音预过滤跳过: {term[:60]}")
            continue

        # 拉取样本
        sample_resp = es.search(index=index, body={
            "query": {"bool": {"must": [
                {"match": {"message": term}},
                {"range": {"@timestamp": {"gte": "now-24h"}}},
            ]}},
            "size": 3,
            "sort": [{"@timestamp": {"order": "desc"}}],
            "_source": ["message", "@timestamp"],
        })
        samples = [
            f"{h['_source'].get('@timestamp', '')}  {h['_source'].get('message', '').split(chr(10))[0][:150]}"
            for h in sample_resp["hits"]["hits"]
        ]

        candidates.append({
            "term": term,
            "doc_count": bucket["doc_count"],
            "score": round(bucket.get("score", 0), 2),
            "samples": samples,
        })

    return candidates


def analyze_with_llm(candidates: list[dict]) -> list[dict]:
    """
    将候选模式送 LLM 单次调用分析，返回带分类的模式列表。
    每个模式：{"term", "type", "priority", "description", "root_cause", "suggested_phrases", "reason"}
    """
    from utils.llm import get_llm
    from langchain_core.messages import SystemMessage, HumanMessage

    if not candidates:
        return []

    llm = get_llm(temperature=0.3)

    system_prompt = f"""你是一个监控系统日志分析专家。你会收到从 ES 中发现的异常日志模式候选列表。

{_EXISTING_RULES_SUMMARY}

你的任务：对每个候选模式进行分类和分析。

分类规则：
- new_rule：近24h出现频次高且显著性分数高，影响业务，值得新建监控规则
- noise：已知或可预期的系统噪音（如连接池重连、定时任务心跳等），建议过滤
- watch：不确定，可能有价值但频次低或信息不足，建议人工关注

输出必须是严格的 JSON，格式如下：
{{
  "patterns": [
    {{
      "term": "原始词条",
      "type": "new_rule | noise | watch",
      "priority": "high | medium | low",
      "description": "一句话中文描述这是什么问题",
      "root_cause": "可能的根因（仅 type=new_rule 且 priority=high 或 medium 时给出，其他省略或为null）",
      "suggested_phrases": ["建议用于 ES 查询的关键词"],
      "reason": "为什么这样分类"
    }}
  ]
}}

规则：
1. 只输出 JSON，不要有任何其他文字
2. 如果候选与已有监控规则高度相似，分类为 watch 并在 reason 中说明
3. suggested_phrases 应该是能精准匹配该问题的关键词，不要太宽泛
"""

    user_content = f"""候选模式列表（来自近24h日志 significant_terms 聚合）：
{json.dumps(candidates, ensure_ascii=False, indent=2)}
"""

    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ])

    raw = response.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    result = json.loads(raw)
    return result.get("patterns", [])


def save_cache(candidates: list[dict], patterns: list[dict]) -> Path:
    """将聚合候选和 LLM 分析结果写入 output/log_analyzer_cache_YYYYMMDD.json。"""
    OUTPUT_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    path = OUTPUT_DIR / f"log_analyzer_cache_{today}.json"
    data = {"candidates": candidates, "patterns": patterns}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"[log_analyzer] 缓存已写入: {path}")
    return path


def load_latest_cache() -> tuple[list[dict], list[dict]] | None:
    """读取最新的 log_analyzer_cache_*.json，无文件返回 None。"""
    files = sorted(OUTPUT_DIR.glob("log_analyzer_cache_*.json"), reverse=True)
    if not files:
        return None
    with open(files[0], encoding="utf-8") as f:
        data = json.load(f)
    logger.info(f"[log_analyzer] 使用缓存: {files[0]}")
    return data["candidates"], data["patterns"]


def _write_noise_rule_to_db(
    rule_type: str,
    description: str,
    must_phrases: list[str],
    priority: str | None,
    suggested_phrases: list[str] | None,
    confirmed_by: str = "operator",
) -> None:
    now = int(time.time() * 1000)
    conn = get_monitor_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO monitor_noise_rules
                  (rule_type, description, must_phrases, priority, suggested_phrases, confirmed_at, confirmed_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    rule_type,
                    description,
                    json.dumps(must_phrases, ensure_ascii=False),
                    priority,
                    json.dumps(suggested_phrases, ensure_ascii=False) if suggested_phrases else None,
                    now,
                    confirmed_by,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _append_noise_to_json(
    term: str,
    description: str,
    suggested_phrases: list[str],
) -> None:
    """将确认的噪音规则写入 noise_rules.json。"""
    if _NOISE_RULES_PATH.exists():
        with open(_NOISE_RULES_PATH, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"_comment": "已确认的日志噪音规则", "rules": []}

    existing_ids = [r.get("id", "") for r in data.get("rules", [])]
    new_id = f"noise_{len(existing_ids) + 1:03d}"

    data.setdefault("rules", []).append({
        "id": new_id,
        "description": description,
        "must_phrases": suggested_phrases,
        "confirmed_at": datetime.now().strftime("%Y-%m-%d"),
        "confirmed_by": "operator",
    })

    with open(_NOISE_RULES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def run_interactive_cli(candidates: list[dict], patterns: list[dict]) -> None:
    """交互式 CLI：逐条展示 LLM 分析结果，由人工确认。"""
    samples_by_term = {c["term"]: c.get("samples", []) for c in candidates}

    actionable = [p for p in patterns if p.get("type") in ("new_rule", "noise")]
    watches = [p for p in patterns if p.get("type") == "watch"]

    recorded = []
    noises_confirmed = []
    skipped = []

    total = len(actionable)

    for i, pat in enumerate(actionable, 1):
        term = pat.get("term", "")
        pat_type = pat.get("type", "")
        priority = pat.get("priority", "low")
        description = pat.get("description", "")
        root_cause = pat.get("root_cause", "")
        suggested_phrases = pat.get("suggested_phrases", [])
        samples = samples_by_term.get(term, [])

        print("\n" + "=" * 60)
        print(f"发现模式 #{i} / {total}   [{priority}] {pat_type}")
        print(f"词条: {term[:80]}")
        print(f"24h 出现: {next((c['doc_count'] for c in candidates if c['term'] == term), '?')} 次   "
              f"显著性分数: {next((c['score'] for c in candidates if c['term'] == term), '?')}")
        print("-" * 60)
        print(f"描述   {description}")
        if root_cause:
            print(f"根因   {root_cause}")
        if suggested_phrases:
            print(f"建议词  {suggested_phrases}")
        if samples:
            print("-" * 60)
            print("样本:")
            for j, s in enumerate(samples[:3], 1):
                print(f"  [{j}] {s[:120]}")
        print("-" * 60)

        if pat_type == "new_rule":
            print("[a] 记录为待建规则   [s] 跳过   [q] 保存退出")
        else:
            print("[n] 确认为噪音   [s] 跳过   [q] 保存退出")

        while True:
            choice = input("> ").strip().lower()
            if choice == "a" and pat_type == "new_rule":
                _write_noise_rule_to_db(
                    rule_type="new_rule",
                    description=description,
                    must_phrases=suggested_phrases,
                    priority=priority,
                    suggested_phrases=suggested_phrases,
                )
                recorded.append(term)
                print(f"  ✓ 已记录为待建规则，建议词: {suggested_phrases}")
                break
            elif choice == "n" and pat_type == "noise":
                _append_noise_to_json(term, description, suggested_phrases)
                _write_noise_rule_to_db(
                    rule_type="noise",
                    description=description,
                    must_phrases=suggested_phrases,
                    priority=priority,
                    suggested_phrases=suggested_phrases,
                )
                noises_confirmed.append(term)
                print("  ✓ 已确认为噪音，noise_rules.json 已更新")
                break
            elif choice == "s":
                skipped.append(term)
                print("  — 已跳过")
                break
            elif choice == "q":
                print("\n已退出，已处理项已保存。")
                _print_cli_summary(recorded, noises_confirmed, skipped, watches)
                return
            else:
                if pat_type == "new_rule":
                    print("  请输入 a / s / q")
                else:
                    print("  请输入 n / s / q")

    _print_cli_summary(recorded, noises_confirmed, skipped, watches)


def _print_cli_summary(
    recorded: list, noises_confirmed: list, skipped: list, watches: list
) -> None:
    print("\n" + "=" * 60)
    print(f"已记录待建规则 {len(recorded)} 条，确认噪音 {len(noises_confirmed)} 条，跳过 {len(skipped)} 条")
    if noises_confirmed:
        print("noise_rules.json 已更新")
    if watches:
        print(f"\n以下 {len(watches)} 个模式标记为 watch（建议人工关注）：")
        for w in watches:
            print(f"  ? {w.get('term', '')[:60]} — {w.get('description', '')}")


def main(interactive: bool = True) -> None:
    if interactive:
        parser = argparse.ArgumentParser(description="日志智能分析器")
        parser.add_argument("--from-cache", action="store_true", help="使用上次缓存，跳过 ES 聚合和 LLM 调用")
        parser.add_argument("--agg-only", action="store_true", help="仅运行 ES 聚合层，不调用 LLM")
        args = parser.parse_args()
    else:
        args = argparse.Namespace(from_cache=False, agg_only=False)

    from config.es import get_es_client, get_index_prefix

    if args.from_cache:
        cached = load_latest_cache()
        if cached is None:
            print("未找到缓存文件，请先运行完整分析。")
            sys.exit(1)
        candidates, patterns = cached
        run_interactive_cli(candidates, patterns)
        return

    es = get_es_client()
    prefix = get_index_prefix()

    print("正在查询 ES 聚合候选模式...")
    candidates = fetch_candidates(es, prefix=prefix)
    print(f"发现 {len(candidates)} 个候选模式（已过滤已知噪音）")

    if args.agg_only:
        print(json.dumps(candidates, ensure_ascii=False, indent=2))
        return

    if not candidates:
        print("未发现新的异常模式，退出。")
        return

    print("正在调用 LLM 分析...")
    patterns = analyze_with_llm(candidates)

    save_cache(candidates, patterns)

    if not interactive:
        logger.info(f"[log_analyzer] 非交互模式完成，发现 {len(patterns)} 个模式，结果已缓存")
        return

    run_interactive_cli(candidates, patterns)


if __name__ == "__main__":
    main()
