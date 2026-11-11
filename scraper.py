"""Page-by-page floorsheet scraper (Playwright).

Why this version is more reliable
---------------------------------
* Every page is VERIFIED before it is accepted: the table must show exactly the expected rows
  (S.No first..last, the right number of rows, and the date column equal to the day being scraped). A half-rendered table, a "Loading..." placeholder or an empty page can
  therefore never be mistaken for data (this was the cause of "Empty page 778").
* A problem on ONE page is healed ON THAT PAGE: wait, press a visible Retry button, click Next again,
  slow down, and only then reload the day and jump back to the page. The day is no longer abandoned
  and restarted from page 1 because one click failed.
* Resuming a day does not need hundreds of Next clicks any more when the site allows a jump
  (address-bar page number, or clicking a numbered page button). Otherwise it steps with Next/Previous,
  verifying every single step, with automatic back-off.
* The scraper slows itself down when the site struggles and speeds up again when it is healthy.
"""
import csv
import io
import pathlib
import re
import time as _time
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Error as PWError
from playwright.sync_api import sync_playwright

from .config import (BACKOFF_SCALE, BASE_URL, BLOCK_ASSETS, DATE_URL_TEMPLATE, DIAG_FILE, LOAD_TIMEOUT, NPT, PACE_MAX, PAGE_DELAY,
                     PAGE_RETRIES, PAGE_SIZE, PARTIAL_FORMAT)
from .parse import parse_date_time, parse_footer, to_output_row
from .storage import State, finalize, partial_path, partial_usable


class Deadline(Exception):
    pass


class ScrapeError(Exception):
    pass


HEADER_KEYS = {"s.no": "sno", "date / time": "datetime", "date/time": "datetime", "contract no": "contract",
               "symbol": "symbol", "buyer": "buyer", "seller": "seller", "quantity": "qty",
               "rate (rs)": "rate", "amount (rs)": "amount"}
DEFAULT_IDX = dict(sno=0, datetime=1, contract=2, symbol=3, buyer=4, seller=5, qty=6, rate=7, amount=8)

# One atomic read of everything we need (rows + footer + a text sample), so the pieces always belong together.
SNAP_JS = r"""() => {
  const rows = [...document.querySelectorAll('table tbody tr')]
    .map(tr => [...tr.querySelectorAll('td')].map(td => td.innerText.trim()))
    .filter(c => c.length >= 9 && /^\d+$/.test(c[0]));
  const text = document.body ? document.body.innerText : '';
  const m = text.match(/Showing\s+[\d,]+\s+to\s+[\d,]+\s+of\s+[\d,]+(?:\.\d+)?\s+transactions/i);
  return {rows: rows, footer: m ? m[0] : null, text: text.slice(0, 20000)};
}"""
HEADER_JS = "() => [...document.querySelectorAll('table thead th')].map(th => th.innerText.trim())"
# Pagination numbers that sit next to the Next button (page-size choices such as 10/25/50 are excluded by a sanity check)
NUMS_JS = r"""() => {
  const all = [...document.querySelectorAll('button, a, [role="button"]')];
  const vis = e => !!(e.offsetWidth || e.offsetHeight) && !e.disabled;
  return all.filter(e => vis(e) && /^\d{1,6}$/.test((e.innerText || '').trim()))
            .map(e => parseInt(e.innerText.trim(), 10));
}"""
NEXT_SEL = 'button:has-text("Next"), a:has-text("Next"), [aria-label*="next" i]'
PREV_SEL = ('button:has-text("Previous"), a:has-text("Previous"), button:has-text("Prev"), a:has-text("Prev"), '
            '[aria-label*="prev" i]')
RETRY_RE = re.compile(r"^\s*(retry|try again|reload|refresh)\s*$", re.I)


def log(msg):
    print(f"[{datetime.now(NPT):%Y-%m-%d %H:%M:%S} NPT] {msg}", flush=True)


@dataclass
class Day:
    d: date
    size: int
    total: int

    @property
    def pages(self):
        return max(1, -(-self.total // self.size))

    def first_of(self, p):                 # S.No of the first row on page p
        return (p - 1) * self.size + 1

    def count_of(self, p):                 # rows on page p
        return min(self.size, self.total - (p - 1) * self.size)


class Scraper:
    def __enter__(self):
        self._pw = sync_playwright().start()
        self._start()
        return self

    def _start(self):
        self.browser = self._pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        ctx = self.browser.new_context(viewport={"width": 1600, "height": 1000})
        if BLOCK_ASSETS:
            ctx.route("**/*", self._route)
        self.page = ctx.new_page()
        self.page.set_default_timeout(60000)
        self.crashed = False
        self.page.on("crash", lambda *_: setattr(self, "crashed", True))
        self.idx = dict(DEFAULT_IDX)
        self.url_key = None        # name of the address-bar page parameter, once learned
        self.url_tried = False
        self.url_failed = False
        self.num_ok = True         # numbered page buttons usable?
        self.pace = PAGE_DELAY     # seconds between pages (rises when the site struggles)
        self.clean = 0

    @staticmethod
    def _route(route):
        if route.request.resource_type in ("image", "media", "font"):
            route.abort()
        else:
            route.continue_()

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

    # ------------------------------------------------------------------ basic helpers
    @staticmethod
    def _check_deadline(deadline):
        if deadline and datetime.now(NPT) >= deadline:
            raise Deadline()

    def _guard(self):
        if self.crashed or self.page.is_closed():
            raise ScrapeError("browser page crashed")

    def _settle(self):
        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except PWError:
            pass  # live ticker may keep the network busy

    def _snap(self):
        try:
            return self.page.evaluate(SNAP_JS)
        except PWError:             # page is navigating right now
            return {"rows": [], "footer": None, "text": ""}

    def _date_of(self, row):
        return parse_date_time(row[self.idx["datetime"]])[0]

    def _read_header(self):
        heads = [h.lower() for h in self.page.evaluate(HEADER_JS)]
        idx = {HEADER_KEYS[h]: i for i, h in enumerate(heads) if h in HEADER_KEYS}
        if len(idx) == 9:
            self.idx = idx
        else:
            log(f"WARNING: unexpected table header {heads}; using default column order")

    def _good(self, attempt):
        """A page that loaded at the first try: after 10 in a row, speed up again (0.25 s)."""
        if attempt == 1:
            self.clean += 1
            if self.clean >= 10 and self.pace > PAGE_DELAY:
                self.pace = max(PAGE_DELAY, self.pace - 0.25)
                self.clean = 0
        else:
            self.clean = 0

    def _bad(self, attempt, why=""):
        """Back off (2,4,8,16,32,45 s) and slow the run down a little - once per incident, not per try."""
        if attempt in (1, 4):
            self.pace = min(PACE_MAX, self.pace + 0.5)
        self.clean = 0
        wait = min(45, 2 ** attempt) * BACKOFF_SCALE
        log(f"  page problem ({why or 'not loaded'}); try {attempt}, waiting {wait:g}s, pace now {self.pace:.1f}s")
        _time.sleep(wait)

    # ------------------------------------------------------------------ opening a day
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

    def _wait_state(self, timeout=90):
        """Return 'data' or 'empty'; raise if the table never settles (never guess 'empty')."""
        end = _time.monotonic() + timeout
        while _time.monotonic() < end:
            snap = self._snap()
            if snap["rows"]:
                return "data"
            foot = parse_footer(snap["footer"] or "")
            if (foot and foot[2] == 0) or re.search(r"no (floorsheet|data|records?|transactions?)\b",
                                                    snap["text"], re.I):
                _time.sleep(3)  # must be stable, not a transient placeholder
                if not self._snap()["rows"]:
                    return "empty"
            _time.sleep(0.5)
        raise ScrapeError("Table did not load (neither rows nor an empty message appeared)")

    def open_date(self, d: date, tries=3):
        last = None
        for i in range(1, tries + 1):
            try:
                self._guard()
                self._apply_date(d)
                state = self._wait_state()
                if state == "data":
                    self._read_header()
                return state
            except (ScrapeError, PWError) as e:
                last = e
                log(f"{d}: opening the page failed ({str(e)[:120]}); try {i}/{tries}")
                if self.crashed:
                    self.restart()
                _time.sleep(5 * i)
        raise ScrapeError(f"could not open {d}: {last}")

    # ------------------------------------------------------------------ verified page reading
    def _page_matches(self, snap, day: Day, p):
        """(ok, reason). ok only if the table shows exactly page p of this day."""
        rows = snap["rows"]
        if not rows:
            return False, "empty"
        want_first, want_n = day.first_of(p), day.count_of(p)
        if rows[0][0] != str(want_first) or len(rows) != want_n or rows[-1][0] != str(want_first + want_n - 1):
            return False, "rows"
        # (the footer text is deliberately NOT required to agree: row numbers + count + date already prove the page,
        #  and a footer that updates a moment later must never block good data)
        ci = self.idx["contract"]
        if any(not r[ci] for r in rows):             # contract number is never legitimately blank
            return False, "blank-cell"
        try:
            if self._date_of(rows[0]) != day.d.isoformat() or self._date_of(rows[-1]) != day.d.isoformat():
                return False, "date"
        except ValueError:
            return False, "date-unreadable"
        return True, ""

    def _wait_page(self, day: Day, p, timeout=None):
        """Wait until the table shows page p. Returns the rows, or None on timeout.
        Raises ScrapeError at once if the page persistently shows ANOTHER DATE (filter not applied)."""
        end = _time.monotonic() + (LOAD_TIMEOUT if timeout is None else timeout)
        snap = None
        wrong_date_since = None
        while _time.monotonic() < end:
            snap = self._snap()
            ok, why = self._page_matches(snap, day, p)
            if ok:
                return snap["rows"]
            if why == "date":
                wrong_date_since = wrong_date_since or _time.monotonic()
                if _time.monotonic() - wrong_date_since > 6:
                    raise ScrapeError(f"page {p} shows another date - the date filter is not applied")
            else:
                wrong_date_since = None
            _time.sleep(0.25)
        self._last_snap = snap
        return None

    def _current_page(self, day: Day):
        """Page number the table shows right now (verified), or None."""
        rows = self._snap()["rows"]
        if not rows:
            return None
        try:
            first = int(rows[0][0])
        except ValueError:
            return None
        if (first - 1) % day.size:
            return None
        p = (first - 1) // day.size + 1
        if p < 1 or p > day.pages:
            return None
        ok, _ = self._page_matches(self._snap(), day, p)
        return p if ok else None

    # ------------------------------------------------------------------ clicking
    def _button(self, sel, wait=10):
        """An ENABLED button, waiting while the page loads/hydrates; None if none appears."""
        end = _time.monotonic() + wait
        while True:
            try:
                btn = self.page.locator(sel).first
                if btn.count() and btn.is_enabled() and btn.get_attribute("aria-disabled") != "true":
                    return btn
            except PWError:
                pass
            if _time.monotonic() >= end:
                return None
            _time.sleep(0.4)

    def _click(self, direction=+1):
        btn = self._button(NEXT_SEL if direction > 0 else PREV_SEL)
        if btn is None:
            return False
        try:
            btn.click(timeout=10000)
        except PWError:
            try:                                  # an overlay may be in the way: click through the DOM
                btn.evaluate("e => e.click()")
            except PWError:
                return False
        return True

    def _heal(self):
        """The table is empty / the page shows an error: press a visible Retry-like button if there is one."""
        for role in ("button", "link"):
            try:
                loc = self.page.get_by_role(role, name=RETRY_RE)
                if loc.count():
                    loc.first.click(timeout=3000)
                    return True
            except PWError:
                pass
        return False

    # ------------------------------------------------------------------ moving between pages
    def _advance(self, day: Day, p, deadline, allow_resync=True):
        """Currently on page p-1 (or p already): get page p VERIFIED. Heals problems on this page."""
        cur_first, nxt_first = day.first_of(p - 1), day.first_of(p)
        why = ""
        for attempt in range(1, PAGE_RETRIES + 1):
            self._check_deadline(deadline)
            self._guard()
            snap = self._snap()
            rows = snap["rows"]
            first = int(rows[0][0]) if rows else None
            if first == nxt_first:                       # arrived (maybe the click was slower than we waited)
                got = self._wait_page(day, p, 20)
                if got:
                    self._good(attempt)
                    return got
                why = "page shown but incomplete"
            elif first == cur_first:                     # still on the old page: click Next
                if self._click(+1):
                    got = self._wait_page(day, p, LOAD_TIMEOUT)
                    if got:
                        self._good(attempt)
                        return got
                    why = "page did not load after Next"
                else:
                    why = "Next button not available"
                    self._heal()
            elif rows:                                   # some OTHER page: clicks doubled / site jumped
                why = f"unexpected page (row {first})"
                if not allow_resync:
                    raise ScrapeError(why)
                self._diag(why, day)
                return self._goto_page(day, p, deadline)
            else:                                        # empty / loading / error card
                why = "table empty or page showing an error"
                if self._heal():
                    _time.sleep(2)
                elif attempt in (2, 3):
                    self._bounce(day, p)                 # cheap fix: step back one page, the loop then clicks Next again
                if attempt >= 4:
                    why += "; reloading"
                    if not allow_resync:
                        raise ScrapeError(why)
                    self._diag(why, day)
                    return self._goto_page(day, p, deadline)
            if attempt >= 3:
                self._diag(why, day, shot=(attempt == 3))
            self._bad(attempt, why)
        self._diag(f"gave up on page {p}: {why}", day)
        raise ScrapeError(f"page {p} did not load after {PAGE_RETRIES} tries ({why})")

    def _bounce(self, day: Day, p):
        """The table is empty although a pager is still shown: go back one page (verified) so that the next
        Next click re-fetches the missing page. Much cheaper than reloading the whole day."""
        try:
            if p - 1 >= 1 and self._click(-1):
                return self._wait_page(day, p - 1, LOAD_TIMEOUT) is not None
        except PWError:
            pass
        return False

    def _goto_page(self, day: Day, p, deadline):
        """Show page p (verified) starting from whatever is on screen; reopens the day when the position is lost."""
        for rnd in range(1, 4):
            self._check_deadline(deadline)
            try:
                if rnd > 1 or self._current_page(day) is None:
                    if self.open_date(day.d) == "empty":
                        raise ScrapeError(f"{day.d} showed no data while resuming")
                got = self._travel(day, p, deadline)
                if got:
                    return got
            except ScrapeError as e:
                if "another date" in str(e) or "showed no data" in str(e):
                    raise
                log(f"  moving to page {p} failed ({str(e)[:100]}); round {rnd}/3")
            self._bad(rnd, f"could not reach page {p}")
        raise ScrapeError(f"could not reach page {p}")

    def _travel(self, day: Day, p, deadline):
        cur = self._current_page(day)
        if cur is None:
            raise ScrapeError("lost position")
        guard = 0
        while cur != p:
            guard += 1
            if guard > day.pages * 2 + 20:
                raise ScrapeError("travel does not converge")
            self._check_deadline(deadline)
            moved = None
            if self.url_key and not self.url_failed:
                moved = self._try_url(day, p)
            if moved is None and self.num_ok:
                moved = self._try_number(day, cur, p)
            if moved is None:
                url_before = self.page.url
                got = self._step(day, cur, p, deadline)
                if got == cur + 1 and not self.url_tried:      # learn the address-bar page parameter while travelling
                    self._learn_url(url_before, self.page.url, got)
            cur = self._current_page(day)
            if cur is None:
                raise ScrapeError("lost position while travelling")
            if cur == p:
                break
            if guard % 100 == 0:
                log(f"  {day.d}: travelling... at page {cur:,} of {p:,}")
        return self._wait_page(day, p, LOAD_TIMEOUT)

    def _step(self, day: Day, cur, target, deadline):
        """One verified Next/Previous click towards target."""
        if target > cur:
            self._advance(day, cur + 1, deadline, allow_resync=False)
            return cur + 1
        for attempt in range(1, 4):
            if self._click(-1) and self._wait_page(day, cur - 1, LOAD_TIMEOUT):
                return cur - 1
            self._bad(attempt, "Previous failed")
        raise ScrapeError("Previous button does not work")

    def _try_number(self, day: Day, cur, target):
        """Click the visible page-number button nearest to the target page (only if it clearly is a pager)."""
        try:
            nums = sorted(set(self.page.evaluate(NUMS_JS)))
        except PWError:
            return None
        # sanity: a real pager shows a run of >=3 consecutive numbers that includes the current page
        if cur not in nums or not ({cur - 1, cur + 1} & set(nums)):
            return None
        cand = [n for n in nums if 1 <= n <= day.pages and abs(n - target) < abs(cur - target)]
        if not cand:
            return None
        n = min(cand, key=lambda x: abs(x - target))
        if abs(n - cur) < 2:
            return None                                  # a plain Next/Previous is just as good
        loc = self.page.locator('button, a, [role="button"]').filter(has_text=re.compile(rf"^\s*{n}\s*$")).first
        try:
            loc.click(timeout=8000)
        except PWError:
            self.num_ok = False
            return None
        if self._wait_page(day, n, LOAD_TIMEOUT):
            return n
        self.num_ok = False
        log("  numbered page buttons did not behave as expected - using Next/Previous instead")
        return None

    def _try_url(self, day: Day, p):
        u = urlsplit(self.page.url)
        q = dict(parse_qsl(u.query))
        q[self.url_key] = str(p)
        try:
            self.page.goto(urlunsplit(u._replace(query=urlencode(q))), wait_until="domcontentloaded")
            self._settle()
            if self._wait_page(day, p, LOAD_TIMEOUT):
                return p
        except (ScrapeError, PWError):
            pass
        self.url_failed = True
        log("  jumping by address-bar page number did not work - using other methods")
        return None

    def _learn_url(self, before, after, p):
        """After the first successful Next click (page p-1 -> p): if the address bar now carries the page number
        (e.g. 'page=2'), remember the parameter name so that a later resume can jump straight to a page."""
        self.url_tried = True
        if before == after:
            return
        qb, qa = dict(parse_qsl(urlsplit(before).query)), dict(parse_qsl(urlsplit(after).query))
        keys = [k for k, v in qa.items()
                if v == str(p) and (qb.get(k) == str(p - 1) or (qb.get(k) is None and p == 2))]
        if len(keys) == 1:
            self.url_key = keys[0]
            log(f"  address bar carries the page number ({keys[0]}=N): fast resume enabled")

    # ------------------------------------------------------------------ diagnostics
    def _diag(self, why, day=None, shot=False):
        """Write what the page looks like (small text file, committed with state/) so failures can be understood."""
        try:
            snap = self._snap()
            rows = snap["rows"]
            btns = self.page.evaluate("""() => [...document.querySelectorAll('button, a, [role="button"]')]
                .filter(e => !!(e.offsetWidth || e.offsetHeight)).slice(0, 40)
                .map(e => ((e.innerText || e.getAttribute('aria-label') || '').trim().slice(0, 16)
                           + (e.disabled ? '(disabled)' : '')))""")
            lines = [f"time: {datetime.now(NPT):%Y-%m-%d %H:%M:%S} NPT", f"why: {why}",
                     f"day: {day.d if day else '-'} size={day.size if day else '-'} total={day.total if day else '-'}",
                     f"url: {self.page.url}", f"footer: {snap['footer']}",
                     f"rows on screen: {len(rows)} first={rows[0][0] if rows else '-'} last={rows[-1][0] if rows else '-'}",
                     f"buttons: {btns}", "text sample:", " ".join(snap["text"].split())[:700]]
            DIAG_FILE.parent.mkdir(parents=True, exist_ok=True)
            DIAG_FILE.write_text("\n".join(lines) + "\n")
            if shot:
                pathlib.Path("probe_out").mkdir(exist_ok=True)
                self.page.screenshot(path="probe_out/page_failure.png", full_page=False)
        except Exception as e:  # noqa
            log(f"DIAG failed: {e}")

    # ------------------------------------------------------------------ main per-day routine
    def scrape_day(self, d: date, deadline, state: State):
        """Returns ('empty', 0) or ('saved', rows). Raises Deadline / ScrapeError."""
        if self.open_date(d) == "empty":
            return "empty", 0
        snap = self._snap()
        foot = parse_footer(snap["footer"] or "")
        if not foot or not snap["rows"]:
            raise ScrapeError("Could not find 'Showing X to Y of N transactions' text")
        total = foot[2]
        size = (foot[1] - foot[0] + 1) if total > foot[1] else PAGE_SIZE
        if size < 1:
            raise ScrapeError(f"implausible page size {size}")
        day = Day(d, max(size, 1), total)
        key = d.isoformat()
        part = partial_path(d)
        meta = state.data["partial"].get(key)
        pages_done, byte_ok = 0, 0
        if meta and meta.get("fmt") != PARTIAL_FORMAT:
            log(f"{d}: discarding old-format partial file (Amount format changed) - restarting day")
            meta = None
        if meta and (meta.get("total") != total or meta.get("size", day.size) != day.size):
            meta = None
        if meta and partial_usable(part, meta):
            pages_done, byte_ok = meta["pages"], meta["bytes"]
            with open(part, "r+b") as f:
                f.truncate(byte_ok)
            log(f"{d}: resuming after page {pages_done} ({min(pages_done * day.size, total):,}/{total:,} rows)")
        else:
            part.write_bytes(b"")
        log(f"{d}: site reports {total:,} transactions ({day.pages:,} pages of {day.size})")

        p = pages_done + 1
        if p > day.pages:                       # everything was already saved, only finalising was missing
            rows = None
        elif p == 1:
            rows = self._wait_page(day, 1, LOAD_TIMEOUT)
            if rows is None:
                self._diag("first page never verified", day)
                raise ScrapeError("page 1 did not show the expected rows")
        else:
            rows = self._goto_page(day, p, deadline)

        f = open(part, "ab")
        try:
            while rows is not None:
                self._check_deadline(deadline)
                out = [to_output_row(r, self.idx) for r in rows]
                if any(r[8] != d.isoformat() for r in out):
                    raise ScrapeError(f"Page {p} contains rows for another date - date filter not applied")
                buf = io.StringIO()
                csv.writer(buf, lineterminator="\n").writerows(out)
                f.write(buf.getvalue().encode())
                f.flush()
                state.data["partial"][key] = {"pages": p, "bytes": f.tell(), "total": total,
                                              "size": day.size, "fmt": PARTIAL_FORMAT}
                if p % 25 == 0:
                    state.save()
                if p % 100 == 0:
                    log(f"{d}: page {p:,}/{day.pages:,}, {min(p * day.size, total):,}/{total:,} rows")
                if p >= day.pages:
                    break
                url_before = self.page.url
                rows = self._advance(day, p + 1, deadline)
                p += 1
                if not self.url_tried:
                    self._learn_url(url_before, self.page.url, p)
                if self.pace:
                    _time.sleep(self.pace)
        except (Deadline, ScrapeError, PWError):
            state.save()          # keep progress so the next attempt resumes
            raise
        finally:
            f.close()

        ok, msg = finalize(d, total)
        state.data["partial"].pop(key, None)
        if not ok:
            partial_path(d).unlink(missing_ok=True)
            raise ScrapeError(f"{d}: verification FAILED - {msg}")
        log(f"{d}: {msg}")
        return "saved", total
