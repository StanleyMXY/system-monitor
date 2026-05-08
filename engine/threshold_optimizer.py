import argparse
import concurrent.futures
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from config.db import get_monitor_conn
from engine.threshold_config import load_thresholds

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"
OUTPUT_DIR = Path(__file__).parent.parent / "output"

_DOMAIN_DESCRIPTIONS = {
    "payment": "支付域负责充值、提现、渠道账号余额管理。critical 级别影响资金安全，warning 提示运营关注。",
    "game": "游戏供应商域负责游戏余额转账和厂商对账。critical 级别表示对账差异持续累积。",
    "risk": "风控域负责高危预警和风控事件处理。所有告警需人工及时响应。",
    "activity": "活动域负责兑换任务和首充监控。告警表示活动配置或流程异常。",
    "account": "用户账户域监控冻结余额异常增长。warning 提示可能存在批量操作风险。",
    "operation": "系统操作域监控 VIP 调整、余额调整、配置变更。enqueue 类告警需人工审批。",
}

_MIN_SAMPLES = 100


def compute_stats(
    metric: str,
    domain: str,
    rows: list[dict],
    thresholds: dict,
) -> dict | None:
    """
    对单个指标的历史行列表计算统计摘要。
    rows: list of {"value": float, "level": str, "recorded_at": int}
    thresholds: 该指标的阈值 dict，如 {"warning": 0.80, "critical": 0.60}
    返回 None 表示数据不足。
    """
    if len(rows) < _MIN_SAMPLES:
        return None

    values = [float(r["value"]) for r in rows]
    sorted_vals = sorted(values)
    n = len(sorted_vals)

    def percentile(p):
        idx = int(n * p / 100)
        return sorted_vals[min(idx, n - 1)]

    p50 = percentile(50)
    p95 = percentile(95)
    p99 = percentile(99)

    alert_levels = {"warning", "critical"}
    alert_rows = [r for r in rows if r["level"] in alert_levels]

    cutoff_7d = int(time.time() * 1000) - 7 * 86400 * 1000
    alert_7d = [r for r in alert_rows if r["recorded_at"] >= cutoff_7d]

    alert_count_30d = len(alert_rows)
    alert_rate_30d = alert_count_30d / n
    alert_rate_7d = len(alert_7d) / max(
        len([r for r in rows if r["recorded_at"] >= cutoff_7d]), 1
    )

    current_warning = thresholds.get("warning")
    current_critical = thresholds.get("critical")
    gap = (p95 - current_warning) if current_warning is not None else None

    return {
        "metric": metric,
        "domain": domain,
        "sample_count": n,
        "p50": round(p50, 4),
        "p95": round(p95, 4),
        "p99": round(p99, 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "alert_rate_7d": round(alert_rate_7d, 6),
        "alert_rate_30d": round(alert_rate_30d, 6),
        "alert_count_30d": alert_count_30d,
        "current_warning": current_warning,
        "current_critical": current_critical,
        "gap_p95_vs_warning": round(gap, 4) if gap is not None else None,
    }


from engine.threshold_config import METRIC_THRESHOLD_MAP as _METRIC_THRESHOLD_MAP


def _extract_threshold(domain_cfg: dict, sub_key: str, key_map: dict) -> dict:
    """从域阈值 JSON 提取 warning/critical 数值。"""
    sub = domain_cfg.get(sub_key, {})
    result = {}
    for role, json_key in key_map.items():
        if json_key in sub:
            result[role] = sub[json_key]
    return result


def load_all_stats(domain_thresholds: dict) -> dict[str, list[dict]]:
    """
    从 monitor_metric_history 读取过去 30 天数据，
    对每个指标计算统计摘要，按域分组返回。
    domain_thresholds: {domain: thresholds_dict}
    返回: {domain: [stats_dict, ...]}
    """
    cutoff = int((time.time() - 30 * 86400) * 1000)
    conn = get_monitor_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT domain, metric, channel_id, value, level, recorded_at
                FROM monitor_metric_history
                WHERE recorded_at >= %s
                ORDER BY metric, recorded_at
                """,
                (cutoff,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    grouped: dict[str, list] = defaultdict(list)
    for row in rows:
        grouped[row["metric"]].append(row)

    result: dict[str, list] = defaultdict(list)
    for metric, metric_rows in grouped.items():
        if metric not in _METRIC_THRESHOLD_MAP:
            continue
        domain, sub_key, key_map = _METRIC_THRESHOLD_MAP[metric]
        domain_cfg = domain_thresholds.get(domain, {})
        threshold = _extract_threshold(domain_cfg, sub_key, key_map)
        stats = compute_stats(metric, domain, metric_rows, threshold)
        if stats is not None:
            result[domain].append(stats)

    return dict(result)


def _call_domain_llm(domain: str, stats_list: list[dict], domain_cfg: dict) -> dict:
    from utils.llm import get_llm
    from langchain_core.messages import SystemMessage, HumanMessage

    llm = get_llm(temperature=0.3)

    system_prompt = f"""你是一个监控系统阈值优化专家。
{_DOMAIN_DESCRIPTIONS.get(domain, "")}

你的任务：根据提供的历史统计数据，分析当前阈值是否合理，并给出建议。

输出必须是严格的 JSON，格式如下：
{{
  "domain": "{domain}",
  "recommendations": [
    {{
      "metric": "指标名",
      "action": "adjust|keep|insufficient_data",
      "current": {{"warning": 数值或null, "critical": 数值或null}},
      "suggested": {{"warning": 数值或null, "critical": 数值或null}},
      "reason": "中文理由，说明为什么这样调整，引用具体统计数字"
    }}
  ]
}}

action 枚举：
- adjust：建议调整阈值，suggested 必须有具体数值
- keep：当前阈值合理，suggested 与 current 相同
- insufficient_data：数据不足，无法给出建议

规则：
1. 只输出 JSON，不要有任何其他文字
2. 所有数值保留 4 位小数
3. reason 必须引用统计数据中的具体数字（p95、告警频率等）
"""

    user_content = f"""域：{domain}
当前阈值配置：
{json.dumps(domain_cfg, ensure_ascii=False, indent=2)}

各指标历史统计（过去30天）：
{json.dumps(stats_list, ensure_ascii=False, indent=2)}
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
    return json.loads(raw)


def _call_cross_domain_llm(domain_reports: list[dict], cross_alerts: list[dict]) -> dict:
    from utils.llm import get_llm
    from langchain_core.messages import SystemMessage, HumanMessage

    llm = get_llm(temperature=0.3)

    system_prompt = """你是一个监控系统跨域关联分析专家。
你会收到多个域的阈值调整建议报告，以及跨域同日告警记录。

你的任务：识别不同域之间的告警时序关联，对已有的建议补充注解或警告。

重要规则：
1. 你只能补充注解，不能推翻已有建议
2. 注解应说明跨域关联的具体情况（哪两个域在哪天同时告警）
3. 如果没有明显关联，返回空列表
4. 只输出 JSON，格式：{"cross_domain_notes": [{"metric": "指标名", "note": "注解内容"}]}
"""

    user_content = f"""域级分析报告：
{json.dumps(domain_reports, ensure_ascii=False, indent=2)}

跨域同日告警记录（近30天）：
{json.dumps(cross_alerts, ensure_ascii=False, indent=2)}
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
    return json.loads(raw)


def load_cross_alert_overlap() -> list[dict]:
    cutoff = int((time.time() - 30 * 86400) * 1000)
    conn = get_monitor_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    FROM_UNIXTIME(recorded_at / 1000, '%%Y-%%m-%%d') AS date,
                    GROUP_CONCAT(DISTINCT domain ORDER BY domain) AS domains,
                    GROUP_CONCAT(DISTINCT metric ORDER BY metric) AS metrics
                FROM monitor_metric_history
                WHERE recorded_at >= %s AND level IN ('warning', 'critical')
                GROUP BY FROM_UNIXTIME(recorded_at / 1000, '%%Y-%%m-%%d')
                HAVING COUNT(DISTINCT domain) >= 2
                ORDER BY date DESC
                LIMIT 30
                """,
                (cutoff,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "date": r["date"],
            "domains": r["domains"].split(","),
            "metrics": r["metrics"].split(","),
        }
        for r in rows
    ]


def run_llm_analysis(
    domain_stats: dict[str, list[dict]],
    domain_thresholds: dict[str, dict],
) -> tuple[list[dict], dict]:
    domains = list(domain_stats.keys())
    domain_reports = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(
                _call_domain_llm,
                domain,
                domain_stats[domain],
                domain_thresholds.get(domain, {}),
            ): domain
            for domain in domains
        }
        for future in concurrent.futures.as_completed(futures):
            domain = futures[future]
            try:
                report = future.result()
                domain_reports.append(report)
                logger.info(f"[optimizer] 域级分析完成: {domain}")
            except Exception as exc:
                logger.error(f"[optimizer] 域级分析失败 [{domain}]: {exc}")

    cross_alerts = load_cross_alert_overlap()
    cross_result = _call_cross_domain_llm(domain_reports, cross_alerts)
    logger.info("[optimizer] 跨域综合分析完成")

    return domain_reports, cross_result


def save_cache(domain_reports: list[dict], cross_result: dict) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    path = OUTPUT_DIR / f"optimizer_cache_{today}.json"
    data = {"domain_reports": domain_reports, "cross_result": cross_result}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"[optimizer] 缓存已写入: {path}")
    return path


def load_latest_cache() -> tuple[list[dict], dict] | None:
    files = sorted(OUTPUT_DIR.glob("optimizer_cache_*.json"), reverse=True)
    if not files:
        return None
    with open(files[0], encoding="utf-8") as f:
        data = json.load(f)
    logger.info(f"[optimizer] 使用缓存: {files[0]}")
    return data["domain_reports"], data["cross_result"]


def _apply_suggestion(metric: str, suggested: dict) -> None:
    if metric not in _METRIC_THRESHOLD_MAP:
        logger.warning(f"[optimizer] 无法定位 {metric} 的配置文件，跳过写入")
        return

    domain, sub_key, key_map = _METRIC_THRESHOLD_MAP[metric]
    path = CONFIG_DIR / f"thresholds_{domain}.json"

    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)

    for role, value in suggested.items():
        if value is None:
            continue
        for r, jk in key_map.items():
            if r == role:
                cfg.setdefault(sub_key, {})[jk] = value

    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def run_interactive_cli(domain_reports: list[dict], cross_result: dict) -> None:
    notes_by_metric: dict[str, str] = {
        n["metric"]: n["note"]
        for n in cross_result.get("cross_domain_notes", [])
    }

    adjustments = [
        rec
        for report in domain_reports
        for rec in report.get("recommendations", [])
        if rec.get("action") == "adjust"
    ]

    keeps = [
        rec
        for report in domain_reports
        for rec in report.get("recommendations", [])
        if rec.get("action") == "keep"
    ]

    if not adjustments:
        print("\n所有阈值均在合理范围内，无需调整。")
        if keeps:
            print(f"\n以下 {len(keeps)} 个指标无需调整：")
            for rec in keeps:
                print(f"  ✓ {rec['metric']}")
        return

    adopted = []
    skipped = []
    edited = []
    updated_files: set[str] = set()

    total = len(adjustments)
    for i, rec in enumerate(adjustments, 1):
        metric = rec["metric"]
        current = rec.get("current", {})
        suggested = rec.get("suggested", {})
        reason = rec.get("reason", "")
        note = notes_by_metric.get(metric, "")

        print("\n" + "=" * 60)
        print(f"建议 #{i} / {total}   [{rec.get('domain', '')}] {metric}")
        print("-" * 60)

        def fmt_thresh(d):
            parts = []
            if d.get("warning") is not None:
                parts.append(f"warning={d['warning']}")
            if d.get("critical") is not None:
                parts.append(f"critical={d['critical']}")
            return "   ".join(parts) if parts else "—"

        print(f"当前阈值   {fmt_thresh(current)}")
        print(f"建议阈值   {fmt_thresh(suggested)}")
        print(f"理  由   {reason}")
        if note:
            print(f"⚠ 跨域   {note}")
        print("-" * 60)
        print("[a] 采纳   [s] 跳过   [e] 编辑数值   [d] 完整信息   [q] 保存退出")

        while True:
            choice = input("> ").strip().lower()
            if choice == "d":
                print(json.dumps(rec, ensure_ascii=False, indent=2))
                continue
            elif choice == "a":
                _apply_suggestion(metric, suggested)
                adopted.append(metric)
                if metric in _METRIC_THRESHOLD_MAP:
                    domain = _METRIC_THRESHOLD_MAP[metric][0]
                    updated_files.add(f"thresholds_{domain}.json")
                print("  ✓ 已采纳")
                break
            elif choice == "s":
                skipped.append(metric)
                print("  — 已跳过")
                break
            elif choice == "e":
                new_suggested = dict(suggested)
                for role in ("warning", "critical"):
                    if suggested.get(role) is not None:
                        val = input(f"  输入新 {role} 值（当前建议 {suggested[role]}，回车跳过）: ").strip()
                        if val:
                            try:
                                new_suggested[role] = float(val)
                            except ValueError:
                                print(f"  无效数值，保留原建议值 {suggested[role]}")
                _apply_suggestion(metric, new_suggested)
                edited.append(metric)
                if metric in _METRIC_THRESHOLD_MAP:
                    domain = _METRIC_THRESHOLD_MAP[metric][0]
                    updated_files.add(f"thresholds_{domain}.json")
                print("  ✓ 已编辑并采纳")
                break
            elif choice == "q":
                print("\n已退出，已处理项已保存。")
                _print_summary(adopted, edited, skipped, updated_files, keeps)
                return
            else:
                print("  请输入 a / s / e / q")

    _print_summary(adopted, edited, skipped, updated_files, keeps)


def _print_summary(
    adopted: list, edited: list, skipped: list,
    updated_files: set, keeps: list,
) -> None:
    print("\n" + "=" * 60)
    print(f"已采纳 {len(adopted)} 条 / 编辑 {len(edited)} 条 / 跳过 {len(skipped)} 条")
    if updated_files:
        print(f"已更新：{', '.join(sorted(updated_files))}")
    if keeps:
        print(f"\n以下 {len(keeps)} 个指标无需调整：")
        for rec in keeps:
            print(f"  ✓ {rec['metric']}")


def _check_min_days() -> bool:
    cutoff_7d = int((time.time() - 7 * 86400) * 1000)
    conn = get_monitor_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM monitor_metric_history WHERE recorded_at >= %s",
                (cutoff_7d,),
            )
            row = cur.fetchone()
            return row["cnt"] >= _MIN_SAMPLES
    finally:
        conn.close()


def main(interactive: bool = True) -> None:
    if interactive:
        parser = argparse.ArgumentParser(description="阈值优化器")
        parser.add_argument("--from-cache", action="store_true", help="使用上次 LLM 缓存，跳过重新分析")
        parser.add_argument("--stats-only", action="store_true", help="只输出统计摘要，不调用 LLM")
        args = parser.parse_args()
    else:
        args = argparse.Namespace(from_cache=False, stats_only=False)

    if args.from_cache:
        cached = load_latest_cache()
        if cached is None:
            print("未找到缓存文件，请先运行完整分析。")
            sys.exit(1)
        domain_reports, cross_result = cached
        run_interactive_cli(domain_reports, cross_result)
        return

    if not _check_min_days():
        print("历史数据不足 7 天，优化器需要积累更多数据后再运行。")
        sys.exit(0)

    domains = ["payment", "game", "risk", "activity", "account", "operation"]
    domain_thresholds = {d: load_thresholds(d) for d in domains}

    print("正在计算历史统计数据...")
    domain_stats = load_all_stats(domain_thresholds)

    if args.stats_only:
        print(json.dumps(domain_stats, ensure_ascii=False, indent=2))
        return

    print(f"正在调用 LLM 分析（{len(domain_stats)} 个域）...")
    domain_reports, cross_result = run_llm_analysis(domain_stats, domain_thresholds)

    save_cache(domain_reports, cross_result)

    if not interactive:
        logger.info("[optimizer] 非交互模式，分析结果已缓存，人工运行 --from-cache 查看")
        return

    run_interactive_cli(domain_reports, cross_result)


if __name__ == "__main__":
    main()
