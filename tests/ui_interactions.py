"""Clicks through the dashboard's controls in a headless browser and checks that each one does what it should.

    .venv/bin/python tests/ui_interactions.py        (after `python run.py export`)
Exit code 1 if any check fails or the page logs a JavaScript error.
"""
import functools
import http.server
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def serve():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(ROOT / "dashboard")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    srv = serve()
    url = f"http://127.0.0.1:{srv.server_port}/"
    results, errors = [], []

    def check(name, ok):
        results.append((name, bool(ok)))
        print(f"{'ok  ' if ok else 'FAIL'} {name}")

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 900}, accept_downloads=True)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(url + "#overview")
        pg.wait_for_selector("#nav button", timeout=60000)
        total = pg.inner_text(".scope b")

        pg.select_option("#f-reg", label="India")
        pg.wait_for_timeout(500)
        check("region filter narrows the selection", pg.inner_text(".scope b") != total and "India" in pg.inner_text(".scope"))
        check("filter is kept in the address", "reg=India" in pg.url)

        pg.fill("#f-sku", "BSS-008")
        pg.dispatch_event("#f-sku", "change")
        pg.wait_for_timeout(500)
        check("product search sets the product and its family", "BSS-008" in pg.inner_text(".scope") and pg.input_value("#f-fam") != "")

        pg.click("#f-reset")
        pg.wait_for_timeout(500)
        check("clear filters restores the whole company", pg.inner_text(".scope b") == total)

        pg.keyboard.press("/")
        pg.fill("#pal-q", "Orion")
        pg.wait_for_timeout(200)
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(500)
        check("finder applies a customer filter", "Orion" in pg.inner_text(".scope"))
        pg.click("#f-reset")

        pg.keyboard.press("?")
        check("help panel opens", pg.is_visible("#helpbox .help"))
        pg.keyboard.press("Escape")

        before = pg.evaluate("getComputedStyle(document.body).backgroundColor")
        pg.click("#theme")
        pg.wait_for_timeout(300)
        check("light / dark switch changes the page", pg.evaluate("getComputedStyle(document.body).backgroundColor") != before)
        pg.click("#theme")

        pg.click('#nav button[data-v="replay"]')
        pg.wait_for_selector("#rv")
        pg.click("#rv")
        pg.wait_for_timeout(2500)
        check("backtest replay reveals the actuals", "?" not in pg.inner_text(".score"))

        pg.click('#nav button[data-v="whatif"]')
        pg.wait_for_selector("#pre .chip")
        pg.click("#pre .chip:nth-child(3)")
        pg.wait_for_timeout(400)
        check("what-if preset lowers the forecast", "−" in pg.inner_text("#wi-kpi"))

        pg.click('#nav button[data-v="procure"]')
        pg.wait_for_selector("#slv")
        b90 = pg.inner_text("#p-kpi")
        pg.fill("#slv", "99")
        pg.dispatch_event("#slv", "input")
        pg.wait_for_timeout(400)
        check("service-level slider changes the plan", pg.inner_text("#p-kpi") != b90)

        pg.click('#nav button[data-v="download"]')
        with pg.expect_download() as d:
            pg.click("#d4")
        path = d.value.path()
        lines = Path(path).read_text().splitlines()
        check("submission-format download has the test columns", lines[0] == "Date,SKU_ID,Region,Units_Sold_Forecast" and len(lines) > 50000)

        pg.goto(url + "#forecast?fam=Hydraulic%20Systems&reg=Middle%20East%20%26%20Africa")
        pg.wait_for_selector("#c-fc canvas", timeout=60000)
        check("a shared link opens with its filters", "Hydraulic Systems · Middle East & Africa" in pg.inner_text(".scope"))
        b.close()
    srv.shutdown()
    check("no JavaScript errors", not errors)
    failed = [n for n, ok in results if not ok]
    print("failed:", failed or "none")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
