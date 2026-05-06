import http.server
import json
import threading
import time
from collections import deque
from pathlib import Path
from engine.models import RuleResult

_OUTPUT_DIR = Path(__file__).parent.parent / "output"
_DOMAIN_LABELS = {
    "payment": "支付域",
    "game": "游戏供应商域",
    "risk": "风控域",
    "activity": "活动域",
    "account": "账户域",
    "operation": "系统操作域",
    "log": "日志域",
}
_DOMAIN_ORDER = ["payment", "game", "risk", "activity", "account", "operation", "log"]
_LEVEL_COLOR = {
    "critical": "#e94560",
    "warning": "#f5a623",
    "ok": "#27ae60",
}


def _alert_to_dict(r: RuleResult) -> dict:
    return {
        "level": r.level,
        "domain": r.metric.domain,
        "message": r.message,
    }


class MonitorDashboard:
    def __init__(self, port: int = 8080):
        self._port = port
        self._html_path = _OUTPUT_DIR / "monitor_dashboard.html"
        self.domain_results: dict[str, dict[tuple, RuleResult]] = {}
        self.recent_alerts: deque[RuleResult] = deque(maxlen=50)
        self.domain_alerts: dict[str, deque[RuleResult]] = {
            d: deque(maxlen=100) for d in _DOMAIN_ORDER
        }
        self._last_update: str = "--"
        self._server: http.server.HTTPServer | None = None

    def start(self) -> None:
        if self._port == 0:
            return
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self._render()

        html_dir = str(_OUTPUT_DIR)

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=html_dir, **kwargs)

            def log_message(self, fmt, *args):
                pass

        self._server = http.server.HTTPServer(("0.0.0.0", self._port), Handler)
        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()

    def update(self, domain: str, results: list[RuleResult]) -> None:
        if domain not in self.domain_results:
            self.domain_results[domain] = {}
        for r in results:
            key = (r.metric.metric, r.metric.channel_id)
            self.domain_results[domain][key] = r
        if domain not in self.domain_alerts:
            self.domain_alerts[domain] = deque(maxlen=100)
        for r in results:
            if r.level != "ok":
                self.recent_alerts.appendleft(r)
                self.domain_alerts[domain].appendleft(r)
        self._last_update = time.strftime("%H:%M:%S")
        self._render()

    def get_domain_summary(self, domain: str) -> dict:
        results = self.domain_results.get(domain, {}).values()
        return {
            "critical": sum(1 for r in results if r.level == "critical"),
            "warning": sum(1 for r in results if r.level == "warning"),
            "ok": sum(1 for r in results if r.level == "ok"),
        }

    def _load_pending_suggestions(self) -> dict:
        """读取最新缓存文件，返回待审批的阈值建议和因果链建议。"""
        result = {"threshold": [], "chains": [], "date": "--"}

        # 阈值优化建议
        optimizer_files = sorted(_OUTPUT_DIR.glob("optimizer_cache_*.json"), reverse=True)
        if optimizer_files:
            try:
                with open(optimizer_files[0], encoding="utf-8") as f:
                    data = json.load(f)
                date_str = optimizer_files[0].stem.replace("optimizer_cache_", "")
                result["date"] = date_str
                for domain_report in data.get("domain_reports", []):
                    for rec in domain_report.get("recommendations", []):
                        if rec.get("action") == "adjust":
                            result["threshold"].append({
                                "metric": rec.get("metric", ""),
                                "current": rec.get("current", {}),
                                "suggested": rec.get("suggested", {}),
                                "reason": rec.get("reason", ""),
                            })
            except Exception:
                pass

        # 根因分析建议
        rca_files = sorted(_OUTPUT_DIR.glob("root_cause_*.json"), reverse=True)
        if rca_files:
            try:
                with open(rca_files[0], encoding="utf-8") as f:
                    data = json.load(f)
                suggested = data.get("analysis", {}).get("suggested_new_chains", [])
                for ch in suggested:
                    result["chains"].append({
                        "description": ch.get("description", ""),
                        "cause": ch.get("cause", {}),
                        "effects": ch.get("effects", []),
                    })
            except Exception:
                pass

        return result

    def _render(self) -> None:
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        cards_html = ""
        for domain_key in _DOMAIN_ORDER:
            label = _DOMAIN_LABELS.get(domain_key, domain_key)
            summary = self.get_domain_summary(domain_key)
            if summary["critical"] > 0:
                border_color = _LEVEL_COLOR["critical"]
                status_text = f"⚠ {summary['critical']} CRITICAL"
                status_color = _LEVEL_COLOR["critical"]
            elif summary["warning"] > 0:
                border_color = _LEVEL_COLOR["warning"]
                status_text = f"△ {summary['warning']} WARNING"
                status_color = _LEVEL_COLOR["warning"]
            else:
                border_color = _LEVEL_COLOR["ok"]
                status_text = "✓ 正常"
                status_color = _LEVEL_COLOR["ok"]

            cards_html += f"""
            <div data-domain="{domain_key}" onclick="selectDomain('{domain_key}')"
                 style="background:#0f3460;padding:16px;border-radius:8px;border-left:4px solid {border_color};cursor:pointer">
              <div style="color:#aaa;font-size:12px;margin-bottom:6px">{label}</div>
              <div style="color:{status_color};font-size:18px;font-weight:bold">{status_text}</div>
              <div style="color:#666;font-size:12px;margin-top:4px">
                {summary['critical']} critical &nbsp;|&nbsp; {summary['warning']} warning &nbsp;|&nbsp; {summary['ok']} ok
              </div>
            </div>"""

        # 构建告警数据 JSON，注入到 JS
        all_alerts = [_alert_to_dict(r) for r in list(self.recent_alerts)[:50]]
        domain_alerts_data = {
            d: [_alert_to_dict(r) for r in list(q)]
            for d, q in self.domain_alerts.items()
        }
        domain_alerts_data["all"] = all_alerts
        alerts_json = json.dumps(domain_alerts_data, ensure_ascii=False)

        domain_labels_json = json.dumps(_DOMAIN_LABELS, ensure_ascii=False)

        # 待审批建议面板
        suggestions = self._load_pending_suggestions()
        suggestions_html = self._render_suggestions(suggestions)

        html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="30">
  <title>天工平台 系统监控</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #1a1a2e; color: #ccc; font-family: 'Courier New', monospace; }}
    .header {{ background: #16213e; padding: 12px 24px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #0f3460; }}
    .header h1 {{ color: #e94560; font-size: 16px; }}
    .header .meta {{ color: #555; font-size: 12px; }}
    .cards {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; padding: 20px 24px; }}
    .cards [data-domain]:hover {{ outline: 1px solid #555; }}
    .cards [data-domain].active {{ outline: 2px solid #4a9eff; }}
    .alerts {{ padding: 0 24px 24px; }}
    .alerts-header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }}
    .alerts-header h2 {{ color: #aaa; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; }}
    .tab-label {{ color: #4a9eff; font-size: 12px; }}
    .alert-box {{ background: #0f3460; border-radius: 8px; overflow: hidden; }}
    .alert-row {{ padding: 8px 12px; border-bottom: 1px solid #1a1a2e; font-size: 13px; }}
    .empty {{ padding: 16px; color: #555; text-align: center; }}
    .suggestions {{ padding: 0 24px 24px; }}
    .suggestions h2 {{ color: #aaa; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px; }}
    .suggest-box {{ background: #0f3460; border-radius: 8px; overflow: hidden; }}
    .suggest-section {{ padding: 8px 12px; border-bottom: 1px solid #1a1a2e; }}
    .suggest-section-title {{ color: #4a9eff; font-size: 12px; margin-bottom: 6px; }}
    .suggest-row {{ padding: 6px 0; border-bottom: 1px solid #16213e; font-size: 12px; color: #ccc; }}
    .suggest-row:last-child {{ border-bottom: none; }}
    .suggest-meta {{ color: #666; font-size: 11px; margin-top: 2px; }}
    .suggest-cli {{ padding: 8px 12px; color: #555; font-size: 11px; }}
  </style>
</head>
<body>
  <div class="header">
    <h1>⬤ 天工平台 系统监控</h1>
    <div class="meta">最后更新: {self._last_update} &nbsp;|&nbsp; 每30秒自动刷新</div>
  </div>
  <div class="cards">{cards_html}</div>
  <div class="alerts">
    <div class="alerts-header">
      <h2>最新告警</h2>
      <span class="tab-label" id="tab-label">全部域</span>
      <span style="color:#555;font-size:11px;cursor:pointer" onclick="selectDomain('all')">[重置]</span>
    </div>
    <div class="alert-box" id="alert-list"></div>
  </div>
  {suggestions_html}

<script>
const alertsData = {alerts_json};
const domainLabels = {domain_labels_json};
const levelColors = {{"critical":"#e94560","warning":"#f5a623","ok":"#27ae60"}};

function renderAlerts(domain) {{
  const alerts = alertsData[domain] || [];
  const list = document.getElementById('alert-list');
  const label = document.getElementById('tab-label');
  label.textContent = domain === 'all' ? '全部域' : (domainLabels[domain] || domain);

  if (alerts.length === 0) {{
    list.innerHTML = '<div class="empty">暂无告警</div>';
    return;
  }}
  list.innerHTML = alerts.slice(0, 30).map(r => {{
    const color = levelColors[r.level] || '#aaa';
    const domainLabel = domainLabels[r.domain] || r.domain;
    const domainPart = domain === 'all' ? '<span style="color:#666;font-size:11px;margin:0 8px">[' + domainLabel + ']</span>' : '';
    return '<div class="alert-row">'
      + '<span style="color:' + (levelColors[r.level] || '#aaa') + ';font-weight:bold">' + r.level.toUpperCase() + '</span>'
      + domainPart
      + '<span style="color:#ccc">' + r.message + '</span>'
      + '</div>';
  }}).join('');
}}

function selectDomain(domain) {{
  document.querySelectorAll('[data-domain]').forEach(el => {{
    el.classList.toggle('active', el.dataset.domain === domain);
  }});
  renderAlerts(domain);
  history.replaceState(null, '', '#' + domain);
}}

// 页面加载时恢复上次选中的域
const initDomain = window.location.hash.slice(1) || 'all';
selectDomain(initDomain);
</script>
</body>
</html>"""

        self._html_path.write_text(html, encoding="utf-8")

    def _render_suggestions(self, suggestions: dict) -> str:
        threshold_items = suggestions.get("threshold", [])
        chain_items = suggestions.get("chains", [])
        date = suggestions.get("date", "--")

        if not threshold_items and not chain_items:
            return ""

        rows_html = ""

        if threshold_items:
            items_html = ""
            for item in threshold_items:
                curr = item["current"]
                sugg = item["suggested"]
                curr_str = " / ".join(f"{k}: {v}" for k, v in curr.items() if v is not None)
                sugg_str = " / ".join(f"{k}: {v}" for k, v in sugg.items() if v is not None)
                reason = item.get("reason", "")[:80] + ("…" if len(item.get("reason", "")) > 80 else "")
                items_html += f"""<div class="suggest-row">
                  <span style="color:#f5a623">{item['metric']}</span>
                  &nbsp;{curr_str} → <span style="color:#27ae60">{sugg_str}</span>
                  <div class="suggest-meta">{reason}</div>
                </div>"""
            rows_html += f"""<div class="suggest-section">
              <div class="suggest-section-title">阈值优化建议（{len(threshold_items)} 条）</div>
              {items_html}
            </div>"""

        if chain_items:
            items_html = ""
            for item in chain_items:
                cause = item.get("cause", {})
                effects = item.get("effects", [])
                effects_str = ", ".join(f"{e.get('domain')}.{e.get('metric')}" for e in effects)
                items_html += f"""<div class="suggest-row">
                  <span style="color:#4a9eff">{cause.get('domain')}.{cause.get('metric')}</span>
                  → {effects_str}
                  <div class="suggest-meta">{item.get('description', '')}</div>
                </div>"""
            rows_html += f"""<div class="suggest-section">
              <div class="suggest-section-title">新因果链建议（{len(chain_items)} 条）</div>
              {items_html}
            </div>"""

        total = len(threshold_items) + len(chain_items)
        return f"""
  <div class="suggestions">
    <div class="alerts-header" style="margin-bottom:8px">
      <h2>待审批建议</h2>
      <span style="color:#f5a623;font-size:12px">{total} 条待处理</span>
      <span style="color:#555;font-size:11px">分析日期: {date}</span>
    </div>
    <div class="suggest-box">
      {rows_html}
      <div class="suggest-cli">采纳阈值建议: python -m engine.threshold_optimizer --from-cache &nbsp;|&nbsp; 采纳因果链: python -m engine.root_cause_analyzer --from-cache</div>
    </div>
  </div>"""
