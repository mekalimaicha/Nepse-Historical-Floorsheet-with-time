import csv
import io
import re
import time as _time
from datetime import date, datetime

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from .config import BASE_URL, DATE_URL_TEMPLATE, NPT, PAGE_DELAY, PARTIAL_FORMAT, PAGE_SIZE
from .parse import parse_total, to_output_row
from .storage import State, finalize, partial_path


class Deadline(Exception):
    pass


class ScrapeError(Exception):
    pass


HEADER_KEYS = {"s.no": "sno", "date / time": "datetime", "date/time": "datetime", "contract no": "contract",
               "symbol": "symbol", "buyer": "buyer", "seller": "seller", "quantity": "qty",
               "rate (rs)": "rate", "amount (rs)": "amount"}
DEFAULT_IDX = dict(sno=0, datetime=1, contract=2, symbol=3, buyer=4, seller=5, qty=6, rate=7, amount=8)

ROWS_JS = """() => [...document.querySelectorAll('table tbody tr')]
  .map(tr => [...tr.querySelectorAll('td')].map(td => td.innerText.trim()))
  .filter(c => c.length >= 9 && /^\\d+$/.test(c[0]))"""
HEADER_JS = "() => [...document.querySelectorAll('table thead th')].map(th => th.innerText.trim())"
NEXT_SEL = 'button:has-text("Next"), a:has-text("Next"), [aria-label*="next" i]'


def log(msg):
    print(f"[{datetime.now(NPT):%Y-%m-%d %H:%M:%S} NPT] {msg}", flush=True)


class Scraper:
    def __enter__(self):
        self._pw = sync_playwright().start()
        self._start()
        return self

    def _start(self):
        self.browser = self._pw.chromium.launch(headless=True)
        self.page = self.browser.new_context(viewport={"width": 1600, "height": 1000}).new_page()
        self.page.set_default_timeout(45000)
        self.idx = dict(DEFAULT_IDX)

    def restart(self):
        try:
            self.browser.close()
        except Exception:
            pass
        self._start()

    def __exit__(self, *a):
        try:
            self.browser.close()
        finally:
            self._pw.stop()

    # ---------- page helpers ----------
    def _settle(self):
        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except PWTimeout:
            pass  # live ticker may keep the network busy

    def _rows(self):
        return self.page.evaluate(ROWS_JS)

    def _read_header(self):
        heads = [h.lower() for h in self.page.evaluate(HEADER_JS)]
        idx = {HEADER_KEYS[h]: i for i, h in enumerate(heads) if h in HEADER_KEYS}
        if len(idx) == 9:
            self.idx = idx
        else:
            log(f"WARNING: unexpected table header {heads}; using default column order")

    def _apply_date(self, d: date):
        iso = d.isoformat()
        if DATE_URL_TEMPLATE:
            self.page.goto(DATE_URL_TEMPLATE.format(date=iso), wait_until="domcontentloaded")
            self._settle()
            return
        self.page.goto(BASE_URL, wait_until="domcontentloaded")
        self._settle()
        inp = self.page.locator('input[type="date"]')
        if not inp.count():
            inp = self.page.locator('input[placeholder*="date" i], input[name*="date" i], input[id*="date" i]')
        if not inp.count():
            raise ScrapeError("No date input found on the page. Run `python -m src.probe` and set "
                              "DATE_URL_TEMPLATE or adapt _apply_date().")
        inp.first.fill(iso)
        inp.first.dispatch_event("change")
        btn = self.page.get_by_role("button", name=re.compile(r"^\s*search\s*$", re.I))
        if btn.count():
            btn.first.click()
        else:
            inp.first.press("Enter")
        self._settle()

    def _wait_state(self, timeout=60):
        """Return 'data' or 'empty'; raise if the table never settles (never guess 'empty')."""
        end = _time.monotonic() + timeout
        while _time.monotonic() < end:
            if self._rows():
                return "data"
            txt = self.page.inner_text("body")
            if parse_total(txt) == 0 or re.search(r"no (floorsheet|data|records?|transactions?)\b", txt, re.I):
                _time.sleep(3)  # must be stable, not a transient placeholder
                if not self._rows():
                    return "empty"
            _time.sleep(0.5)
        raise ScrapeError("Table did not load (neither rows nor an empty message appeared)")

    def open_date(self, d: date):
        self._apply_date(d)
        state = self._wait_state()
        if state == "data":
            self._read_header()
        return state

    def _total(self):
        t = parse_total(self.page.inner_text("body"))
        if t is None:
            raise ScrapeError("Could not find 'Showing X to Y of N transactions' text")
        return t

    def _next_button(self, wait=20):
        """Return an ENABLED Next button, waiting while the page loads/hydrates; None if none appears."""
        end = _time.monotonic() + wait
        while True:
            btn = self.page.locator(NEXT_SEL).first
            try:
                if btn.count() and btn.is_enabled() and btn.get_attribute("aria-disabled") != "true":
                    return btn
            except Exception:
                pass
            if _time.monotonic() >= end:
                return None
            _time.sleep(0.5)

    def _diag(self, why):
        """Log what the pagination looks like and save a screenshot (probe_out/)."""
        try:
            els = self.page.locator(NEXT_SEL)
            info = [(els.nth(i).inner_text()[:20], els.nth(i).is_enabled()) for i in range(min(els.count(), 5))]
            log(f"DIAG {why}: Next-candidates={info}")
            import pathlib
            pathlib.Path("probe_out").mkdir(exist_ok=True)
            self.page.screenshot(path="probe_out/next_failure.png", full_page=True)
        except Exception as e:  # noqa
            log(f"DIAG failed: {e}")

    def _next(self, prev_first, wait=20):
        btn = self._next_button(wait)
        if btn is None:
            return False
        btn.click()
        self.page.wait_for_function(
            "p => { const c = document.querySelector('table tbody tr td'); return c && c.innerText.trim() !== p; }",
            arg=prev_first, timeout=30000)
        return True

    def _page_rows(self, expected_full):
        """Read the current page; re-read briefly if a non-final page looks half-rendered."""
        for _ in range(6):
            rows = self._rows()
            if not expected_full or len(rows) == PAGE_SIZE:
                return rows
            _time.sleep(1)
        return rows

    # ---------- main per-day routine ----------
    def scrape_day(self, d: date, deadline, state: State):
        """Returns ('empty', 0) or ('saved', rows). Raises Deadline / ScrapeError."""
        if self.open_date(d) == "empty":
            return "empty", 0
        total = self._total()
        part = partial_path(d)
        meta = state.data["partial"].get(d.isoformat())
        pages_done, byte_ok = 0, 0
        if meta and meta.get("fmt") != PARTIAL_FORMAT:
            log(f"{d}: discarding old-format partial file (Amount format changed) - restarting day")
            meta = None
        if meta and meta.get("total") == total and part.exists():
            pages_done, byte_ok = meta["pages"], meta["bytes"]
            with open(part, "r+b") as f:
                f.truncate(byte_ok)
            log(f"{d}: resuming after page {pages_done} ({pages_done * PAGE_SIZE:,}/{total:,} rows)")
        else:
            part.write_bytes(b"")
        log(f"{d}: site reports {total:,} transactions ({-(-total // PAGE_SIZE):,} pages)")

        page_no = 1
        while page_no <= pages_done:  # fast-forward on resume
            first = self.page.locator("table tbody tr td").first.inner_text().strip()
            if not self._next(first, wait=30):
                self._diag(f"fast-forward stuck at page {page_no}")
                raise ScrapeError(f"Could not fast-forward to resume page (stuck at page {page_no})")
            page_no += 1

        f = open(part, "ab")
        try:
            while True:
                if deadline and datetime.now(NPT) >= deadline:
                    raise Deadline()
                already = (page_no - 1) * PAGE_SIZE
                raw = self._page_rows(already + PAGE_SIZE < total)
                if not raw:
                    raise ScrapeError(f"Empty page {page_no}")
                out = [to_output_row(r, self.idx) for r in raw]
                if any(r[8] != d.isoformat() for r in out):
                    raise ScrapeError(f"Page {page_no} contains rows for another date - date filter not applied")
                buf = io.StringIO()
                csv.writer(buf, lineterminator="\n").writerows(out)
                f.write(buf.getvalue().encode())
                f.flush()
                state.data["partial"][d.isoformat()] = {"pages": page_no, "bytes": f.tell(), "total": total,
                                                         "fmt": PARTIAL_FORMAT}
                if page_no % 25 == 0:
                    state.save()
                if page_no % 200 == 0:
                    log(f"{d}: page {page_no:,}, {already + len(out):,}/{total:,} rows")
                if already + len(out) >= total:
                    break
                first = raw[0][0]
                if not self._next(first):
                    self._diag(f"no Next button after page {page_no}")
                    raise ScrapeError(f"Next button unavailable after page {page_no} "
                                      f"({already + len(out):,}/{total:,} rows) - progress kept, will resume")
                page_no += 1
                if PAGE_DELAY:
                    _time.sleep(PAGE_DELAY)
        except (Deadline, ScrapeError):
            state.save()          # keep progress so the next attempt resumes
            raise
        finally:
            f.close()

        ok, msg = finalize(d, total)
        state.data["partial"].pop(d.isoformat(), None)
        if not ok:
            partial_path(d).unlink(missing_ok=True)
            raise ScrapeError(f"{d}: verification FAILED - {msg}")
        log(f"{d}: {msg}")
        return "saved", total
