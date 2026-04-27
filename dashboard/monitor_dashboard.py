# dashboard/monitor_dashboard.py
# 实时监控看板：HTTP server serve 静态 HTML，scheduler 每轮更新后刷新文件
import http.server
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
}
_LEVEL_COLOR = {
    "critical": "#e94560",
    "warning": "#f5a623",
    "ok": "#27ae60",
}


class MonitorDashboard:
    def __init__(self, port: int = 8080):
        self._port = port
        self._html_path = _OUTPUT_DIR / "monitor_dashboard.html"
        self.domain_results: dict[str, list[RuleResult]] = {}
        self.recent_alerts: deque[RuleResult] = deque(maxlen=50)
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
                pass  # 静默 HTTP 日志

        self._server = http.server.HTTPServer(("0.0.0.0", self._port), Handler)
        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()

    def update(self, domain: str, results: list[RuleResult]) -> None:
        self.domain_results[domain] = results
        for r in results:
            if r.level != "ok":
                self.recent_alerts.appendleft(r)
        self._last_update = time.strftime("%H:%M:%S")
        self._render()

    def get_domain_summary(self, domain: str) -> dict:
        results = self.domain_results.get(domain, [])
        return {
            "critical": sum(1 for r in results if r.level == "critical"),
            "warning": sum(1 for r in results if r.level == "warning"),
            "ok": sum(1 for r in results if r.level == "ok"),
        }

    def _render(self) -> None:
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        cards_html = ""
        for domain_key in ["payment", "game", "risk"]:
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
            <div data-domain="{domain_key}" style="background:#0f3460;padding:16px;border-radius:8px;border-left:4px solid {border_color}">
              <div style="color:#aaa;font-size:12px;margin-bottom:6px">{label}</div>
              <div style="color:{status_color};font-size:18px;font-weight:bold">{status_text}</div>
              <div style="color:#666;font-size:12px;margin-top:4px">
                {summary['critical']} critical &nbsp;|&nbsp; {summary['warning']} warning &nbsp;|&nbsp; {summary['ok']} ok
              </div>
            </div>"""

        alerts_html = ""
        for r in list(self.recent_alerts)[:20]:
            color = _LEVEL_COLOR.get(r.level, "#aaa")
            label = _DOMAIN_LABELS.get(r.metric.domain, r.metric.domain)
            alerts_html += f"""
            <div style="padding:8px 12px;border-bottom:1px solid #1a1a2e;font-size:13px">
              <span style="color:{color};font-weight:bold">[{r.level.upper()}]</span>
              <span style="color:#666;font-size:11px;margin:0 8px">[{label}]</span>
              <span style="color:#ccc">{r.message}</span>
            </div>"""

        if not alerts_html:
            alerts_html = '<div style="padding:16px;color:#555;text-align:center">暂无告警</div>'

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
    .alerts {{ padding: 0 24px 24px; }}
    .alerts h2 {{ color: #aaa; font-size: 13px; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 1px; }}
    .alert-box {{ background: #0f3460; border-radius: 8px; overflow: hidden; }}
  </style>
</head>
<body>
  <div class="header">
    <h1>⬤ 天工平台 系统监控</h1>
    <div class="meta">最后更新: {self._last_update} &nbsp;|&nbsp; 每30秒自动刷新</div>
  </div>
  <div class="cards">{cards_html}</div>
  <div class="alerts">
    <h2>最新告警</h2>
    <div class="alert-box">{alerts_html}</div>
  </div>
</body>
</html>"""

        self._html_path.write_text(html, encoding="utf-8")
