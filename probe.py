"""One-off diagnostic: shows the page's inputs/buttons and the network calls it makes.
Run:  python -m src.probe        (output also saved to probe_out/)"""
from pathlib import Path
from playwright.sync_api import sync_playwright
from .config import BASE_URL

def main():
    out = Path("probe_out"); out.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": 1600, "height": 1000})
        calls = []
        pg.on("response", lambda r: calls.append((r.status, r.request.method, r.url))
              if r.request.resource_type in ("fetch", "xhr") else None)
        pg.goto(BASE_URL, wait_until="domcontentloaded")
        pg.wait_for_timeout(8000)
        print("== inputs ==")
        for e in pg.locator("input, select").all():
            print({k: e.get_attribute(k) for k in ("type", "name", "id", "placeholder", "aria-label")})
        print("== buttons ==")
        for e in pg.locator("button, [role=button]").all():
            print(repr(e.inner_text().strip()[:40]), e.get_attribute("aria-label"))
        print("== XHR/fetch calls ==")
        for c in calls:
            print(*c)
        print("== footer text ==")
        print([l for l in pg.inner_text("body").splitlines() if "Showing" in l])
        pg.screenshot(path=str(out / "floorsheet.png"), full_page=True)
        (out / "page.html").write_text(pg.content())
        b.close()


if __name__ == "__main__":
    main()
