"""Opens the dashboard in a headless browser, visits every view (plus a filtered state), records JavaScript errors and
saves screenshots to outputs/ui/.  Run after `python run.py export`:

    .venv/bin/python tests/ui_check.py            # light theme
    .venv/bin/python tests/ui_check.py dark       # dark theme
Exit code 1 if any view logs an error or draws no chart where one is expected.
"""
import functools
import json
import http.server
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "outputs" / "ui"
VIEWS = ["overview", "brief", "health", "explorer", "anomalies", "stockouts", "forecast", "replay", "lifecycle", "whatif", "procure",
         "exceptions", "drivers", "download"]


def serve():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = functools.partial(Quiet, directory=str(ROOT / "dashboard"))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main(theme="light"):
    SHOTS.mkdir(parents=True, exist_ok=True)
    srv = serve()
    url = f"http://127.0.0.1:{srv.server_port}/"
    problems, results = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, color_scheme=theme)
        page.add_init_script(f"localStorage.setItem('celeris-theme', '{theme}')")
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url + "#overview")
        page.wait_for_selector("#nav button", timeout=60000)
        states = [(v, None) for v in VIEWS] + [("explorer", "fam"), ("forecast", "sku"), ("anomalies", "sku")]
        for view, filt in states:
            errors.clear()
            if filt == "fam":
                page.select_option("#f-fam", "1")
            if filt == "sku":
                page.fill("#f-sku", "BSS-008")
                page.dispatch_event("#f-sku", "change")
                page.select_option("#f-reg", "4")
            page.click(f'#nav button[data-v="{view}"]')
            page.wait_for_timeout(900)
            charts = page.evaluate("document.querySelectorAll('main [_echarts_instance_]').length")
            failed = page.evaluate("!!document.querySelector('main .empty') && document.querySelector('main .empty').textContent")
            name = f"{view}{'_' + filt if filt else ''}_{theme}"
            page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)
            status = "ok"
            if errors:
                status = "JS ERRORS: " + " | ".join(errors[:3])
            elif failed and "No discontinued" not in failed:
                status = "EMPTY/FAILED: " + failed[:120]
            elif view not in ("download", "exceptions", "brief") and charts == 0:
                status = "NO CHARTS"
            print(f"{name:28s} charts={charts:2d}  {status}")
            results.append({"state": name, "charts": charts, "status": status})
            if status != "ok":
                problems.append(name)
            if filt:
                page.click("#f-reset")
                page.wait_for_timeout(300)
        browser.close()
    srv.shutdown()
    (SHOTS / f"results_{theme}.json").write_text(json.dumps(results, indent=1))
    print("problems:", problems or "none")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(*(sys.argv[1:] or ["light"])))
