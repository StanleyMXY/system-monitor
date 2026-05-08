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
_SUGGEST_COLOR = "#4a9eff"


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
        self._detail_path = _OUTPUT_DIR / "suggestions_detail.html"
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

    def _render(self) -> None:
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        suggestions = self._load_pending_suggestions()
        threshold_count = suggestions["threshold"]
        chain_count = suggestions["chains"]
        log_count = suggestions["log_patterns"]
        total = threshold_count + chain_count + log_count

        # 域状态卡片
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

        # 待审批建议卡片
        if total > 0:
            suggest_border = _SUGGEST_COLOR
            suggest_status = f"◈ {total} 条待审批"
            suggest_color = _SUGGEST_COLOR
            suggest_sub = f"{threshold_count} 阈值调整 &nbsp;|&nbsp; {chain_count} 新因果链 &nbsp;|&nbsp; {log_count} 日志模式"
        else:
            suggest_border = "#333"
            suggest_status = "◇ 暂无建议"
            suggest_color = "#555"
            suggest_sub = f"分析日期: {suggestions['date']}"

        cards_html += f"""
            <div onclick="window.open('suggestions_detail.html','_blank')"
                 style="background:#0f3460;padding:16px;border-radius:8px;border-left:4px solid {suggest_border};cursor:pointer">
              <div style="color:#aaa;font-size:12px;margin-bottom:6px">待审批建议</div>
              <div style="color:{suggest_color};font-size:18px;font-weight:bold">{suggest_status}</div>
              <div style="color:#666;font-size:12px;margin-top:4px">{suggest_sub}</div>
            </div>"""

        # 告警数据
        all_alerts = [_alert_to_dict(r) for r in list(self.recent_alerts)[:50]]
        domain_alerts_data = {
            d: [_alert_to_dict(r) for r in list(q)]
            for d, q in self.domain_alerts.items()
        }
        domain_alerts_data["all"] = all_alerts
        alerts_json = json.dumps(domain_alerts_data, ensure_ascii=False)
        domain_labels_json = json.dumps(_DOMAIN_LABELS, ensure_ascii=False)

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
    .cards div:last-child:hover {{ outline: 1px solid #4a9eff; }}
    .alerts {{ padding: 0 24px 24px; }}
    .alerts-header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }}
    .alerts-header h2 {{ color: #aaa; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; }}
    .tab-label {{ color: #4a9eff; font-size: 12px; }}
    .alert-box {{ background: #0f3460; border-radius: 8px; overflow: hidden; }}
    .alert-row {{ padding: 8px 12px; border-bottom: 1px solid #1a1a2e; font-size: 13px; }}
    .empty {{ padding: 16px; color: #555; text-align: center; }}
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

const initDomain = window.location.hash.slice(1) || 'all';
selectDomain(initDomain);
</script>
</body>
</html>"""

        self._html_path.write_text(html, encoding="utf-8")
        self._render_detail_page(suggestions)

    def _render_detail_page(self, suggestions: dict) -> None:
        threshold_count = suggestions.get("threshold", 0)
        chain_count = suggestions.get("chains", 0)
        log_count = suggestions.get("log_patterns", 0)
        date = suggestions.get("date", "--")
        total = threshold_count + chain_count + log_count

        # 阈值建议
        if threshold_count > 0:
            threshold_html = f"""
            <h2 style="color:#aaa;font-size:13px;text-transform:uppercase;letter-spacing:1px;margin-bottom:12px">
              阈值优化建议（{threshold_count} 条待审批）
            </h2>
            <p style="color:#ccc">前往 <a href="http://localhost:8000/suggestions" style="color:#4a9eff">Web 审批界面</a> 查看详情并审批。</p>
            <div class="cli">采纳: python -m engine.threshold_optimizer --from-cache</div>"""
        else:
            threshold_html = "<p style='color:#555'>暂无阈值调整建议</p>"

        # 因果链建议
        if chain_count > 0:
            chains_html = f"""
            <h2 style="color:#aaa;font-size:13px;text-transform:uppercase;letter-spacing:1px;margin:24px 0 12px">
              新因果链建议（{chain_count} 条待审批）
            </h2>
            <p style="color:#ccc">前往 <a href="http://localhost:8000/suggestions" style="color:#4a9eff">Web 审批界面</a> 查看详情并审批。</p>
            <div class="cli">采纳: python -m engine.root_cause_analyzer --from-cache</div>"""
        else:
            chains_html = "<p style='color:#555;margin-top:24px'>暂无新因果链建议</p>"

        # 日志模式建议
        if log_count > 0:
            log_html = f"""
            <h2 style="color:#aaa;font-size:13px;text-transform:uppercase;letter-spacing:1px;margin:24px 0 12px">
              日志模式分析（{log_count} 条待审批）
            </h2>
            <p style="color:#ccc">前往 <a href="http://localhost:8000/suggestions" style="color:#4a9eff">Web 审批界面</a> 查看详情并审批。</p>
            <div class="cli">确认: python -m engine.log_analyzer --from-cache</div>"""
        else:
            log_html = "<p style='color:#555;margin-top:24px'>暂无日志模式待确认</p>"

        html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <title>待审批建议 — 天工平台监控</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #1a1a2e; color: #ccc; font-family: 'Courier New', monospace; padding: 24px; }}
    .header {{ margin-bottom: 24px; }}
    .header h1 {{ color: #4a9eff; font-size: 16px; margin-bottom: 4px; }}
    .header .meta {{ color: #555; font-size: 12px; }}
    table {{ width: 100%; border-collapse: collapse; background: #0f3460; border-radius: 8px; overflow: hidden; margin-bottom: 8px; }}
    th {{ background: #16213e; color: #aaa; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; padding: 8px 12px; text-align: left; }}
    td {{ padding: 8px 12px; font-size: 12px; border-bottom: 1px solid #1a1a2e; vertical-align: top; }}
    tr:last-child td {{ border-bottom: none; }}
    .cli {{ margin-top: 8px; color: #555; font-size: 11px; padding: 6px 0; }}
    .back {{ color: #4a9eff; font-size: 12px; cursor: pointer; margin-bottom: 20px; display: inline-block; }}
    .badge {{ display: inline-block; background: #4a9eff22; color: #4a9eff; border-radius: 4px; padding: 2px 8px; font-size: 12px; margin-left: 8px; }}
  </style>
</head>
<body>
  <div class="header">
    <h1>待审批建议 <span class="badge">{total} 条</span></h1>
    <div class="meta">分析日期: {date} &nbsp;|&nbsp; <a href="monitor_dashboard.html" style="color:#555">← 返回监控看板</a></div>
  </div>
  {threshold_html}
  {chains_html}
  {log_html}
</body>
</html>"""

        self._detail_path.write_text(html, encoding="utf-8")
