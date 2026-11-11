"""Integration tests against a LOCAL fake of the floorsheet website (tests/mock_site.py).
They reproduce the failures seen in production (empty last page, page that stops loading, lost Next button,
swallowed clicks, resume of a long day) and prove the scraper recovers. Needs Chromium:
    pip install -r requirements.txt && playwright install chromium && pytest tests/test_mock_site.py -s
"""
import csv
import os
import sys
from datetime import date, datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import mock_site  # noqa: E402

pytest.importorskip("playwright")
import src.scraper as sc  # noqa: E402
import src.storage as stg  # noqa: E402
from src.scraper import Deadline, ScrapeError, Scraper  # noqa: E402
from src.storage import State  # noqa: E402

D16, D14, D13, D12, D09 = (date(2026, 10, 16), date(2026, 10, 14), date(2026, 10, 13), date(2026, 10, 12),
                           date(2026, 10, 9))


@pytest.fixture
def env(tmp_path, monkeypatch):
    srv = mock_site.start()
    monkeypatch.setattr(sc, "BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}/floorsheet")
    monkeypatch.setattr(sc, "LOAD_TIMEOUT", 5.0)
    monkeypatch.setattr(sc, "BACKOFF_SCALE", 0.05)
    monkeypatch.setattr(sc, "DIAG_FILE", tmp_path / "diag.txt")
    monkeypatch.setattr(stg, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(stg, "PARTIAL_DIR", tmp_path / "partial")
    monkeypatch.chdir(tmp_path)
    mock_site.reset()
    yield tmp_path
    srv.shutdown()


def browser_or_skip():
    try:
        return Scraper().__enter__()
    except Exception as e:  # noqa
        pytest.skip(f"Chromium not available: {e}")


def scrape(d, state=None, seconds=None):
    state = state or State({})
    s = browser_or_skip()
    try:
        deadline = datetime.now(sc.NPT) + timedelta(seconds=seconds) if seconds else None
        return s.scrape_day(d, deadline, state), state, s
    finally:
        s.__exit__()


def check_file(root, d, total):
    f = root / "data" / str(d.year) / f"{d}_Floorsheet.csv"
    rows = list(csv.reader(open(f, newline="")))
    assert rows[0][:3] == ["S.No", "Contract No", "Symbol"]
    body = rows[1:]
    assert len(body) == total
    assert [int(r[0]) for r in body] == list(range(1, total + 1))          # no gap, no repeat
    assert all(r[8] == d.isoformat() for r in body)                        # date column
    assert len({r[1] for r in body}) == total                              # unique contracts
    i = 7
    assert body[i][1] == f"{d.isoformat().replace('-', '')}0100{i + 1:06d}"
    # amount recomputed = quantity x rate (not the abbreviated 'Rs 6.7K')
    q, rt = int(body[i][5]), float(body[i][6])
    assert abs(float(body[i][7]) - q * rt) < 0.01
    return body


def test_clean_day_with_loading_placeholder(env):
    (res, n), _, _ = scrape(D16)
    assert (res, n) == ("saved", 620)
    check_file(env, D16, 620)


def test_empty_last_page_glitch(env):                 # the 'Empty page 778' failure
    mock_site.reset(empty_glitch_last=True)
    (res, n), _, _ = scrape(D16)
    assert res == "saved"
    check_file(env, D16, 620)


def test_transient_server_errors_midway(env):         # a page fails once with an error card (no Next button)
    mock_site.reset(fail_once={6, 13})
    (res, n), _, _ = scrape(D16)
    assert res == "saved"
    check_file(env, D16, 620)


def test_page_stuck_until_reload(env):                # error persists until the page is reloaded
    mock_site.reset(fail_until_reload={6})
    (res, n), _, _ = scrape(D16)
    assert res == "saved"
    check_file(env, D16, 620)


def test_swallowed_next_click(env):
    mock_site.reset(swallow={4, 9})
    (res, n), _, _ = scrape(D16)
    assert res == "saved"
    check_file(env, D16, 620)


def test_single_page_and_exact_multiple(env):
    assert scrape(D09)[0] == ("saved", 50)
    check_file(env, D09, 50)
    mock_site.reset()
    assert scrape(date(2026, 10, 8))[0] == ("saved", 51)
    check_file(env, date(2026, 10, 8), 51)


def test_empty_day(env):
    (res, n), _, _ = scrape(D12)
    assert (res, n) == ("empty", 0)


def test_other_date_is_refused(env):
    mock_site.reset(serve_other_date=True)
    with pytest.raises(ScrapeError):
        scrape(D16)


@pytest.mark.parametrize("mode", [{}, {"numbers": True}, {"urlsync": True}])
def test_resume_long_day(env, mode):
    """Stop a 60-page day part-way (time limit), then resume; result must be perfect, and fast modes must jump."""
    mock_site.reset(latency=0.15, **mode)
    state = State({})
    s = browser_or_skip()
    try:
        with pytest.raises(Deadline):
            s.scrape_day(D13, datetime.now(sc.NPT) + timedelta(seconds=9), state)
    finally:
        s.__exit__()
    done = state.data["partial"][D13.isoformat()]["pages"]
    assert 3 <= done < 60, done
    mock_site.STATE["log"].clear()
    mock_site.reset(latency=0.02, **mode)
    s = browser_or_skip()
    try:
        assert s.scrape_day(D13, None, state) == ("saved", 3000)
    finally:
        s.__exit__()
    check_file(env, D13, 3000)
    calls = len(mock_site.STATE["log"])
    print(f"mode={mode} resumed after page {done}: {calls} api calls for {60 - done} remaining pages")
    if mode:      # fast modes: page 1 + jump + the remaining pages, far fewer than walking from page 1
        assert calls < (60 - done) + done * 0.6 + 6


def test_runner_end_to_end_with_holidays(env, monkeypatch):
    """Whole backfill loop: holidays skipped, empty day confirmed+recorded, --verify-holidays proves/overrides."""
    import types
    import src.report as rp
    import src.runner as rn
    monkeypatch.setattr(rn, "BACKFILL_START", date(2026, 10, 20))
    monkeypatch.setattr(rn, "BACKFILL_END", date(2026, 10, 12))
    monkeypatch.setattr(rp, "BACKFILL_END", date(2026, 10, 12))
    monkeypatch.setattr(rp, "REPORT_DIR", env / "state")
    monkeypatch.setattr(stg, "STATE_FILE", env / "state" / "state.json")
    monkeypatch.setattr(rn, "_time", types.SimpleNamespace(sleep=lambda s: None))
    mock_site.TOTALS["2026-10-15"] = 75
    mock_site.reset()
    # 1) normal run: 10-12 empty (confirmed 3x), 13/14/15/16 saved, Oct 19+20 are listed holidays -> not even opened
    assert rn.run_backfill(30, manual=True) == 0
    st = State.load()
    assert st.data["empty_dates"] == ["2026-10-12"]
    for d, n in ((D13, 3000), (D14, 130), (date(2026, 10, 15), 75), (D16, 620)):
        check_file(env, d, n)
    assert not any(k in ("2026-10-19", "2026-10-20") for _, k in [(0, x[0]) for x in mock_site.STATE["log"]])
    assert st.data["backfill"]["done"]
    rep = (env / "state" / "non_trading_days.csv").read_text()
    assert "2026-10-12,Monday,no data on website" in rep and "2026-10-19,Monday,holiday list: Astami" in rep
    # 2) --verify-holidays: both listed holidays have no data on the site -> confirmed
    assert rn.run_backfill(30, manual=True, verify_holidays=True) == 0
    st = State.load()
    assert sorted(st.data["verified_holidays"]) == ["2026-10-19", "2026-10-20"]
    assert "confirmed empty on website" in (env / "state" / "non_trading_days.csv").read_text()
    # 3) a listed holiday that DOES have data becomes a trading day
    st.data["verified_holidays"].remove("2026-10-20")
    st.save()
    mock_site.TOTALS["2026-10-20"] = 120
    assert rn.run_backfill(30, manual=True, verify_holidays=True) == 0
    st = State.load()
    assert st.data["holiday_overrides"] == ["2026-10-20"]
    check_file(env, date(2026, 10, 20), 120)
    assert rn.trading(date(2026, 10, 20), st)
    mock_site.TOTALS.pop("2026-10-20")
