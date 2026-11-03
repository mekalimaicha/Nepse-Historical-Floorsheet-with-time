"""Entry point:
   python -m src.runner backfill|daily [--max-minutes N] [--manual] [--retry-failed]
--manual: ignore the 7 PM-7 AM window and stop after at most 60 minutes. Just run it again to continue."""
import argparse
import sys
import time as _time
from datetime import date, datetime, time, timedelta

from .config import (BACKFILL_END, BACKFILL_START, DAILY_EARLIEST, DAILY_START, MANUAL_MAX_MINUTES,
                     MAX_ATTEMPTS_PER_RUN, MAX_FAILED_RUNS, NPT, WINDOW_END, WINDOW_START)
from .scraper import Deadline, Scraper, log
from .storage import State, day_done
from .trading_calendar import is_trading_day, next_trading_day


def in_night_window(t: datetime) -> bool:
    return t.time() >= WINDOW_START or t.time() < WINDOW_END


def backfill_deadline(now: datetime, max_minutes: int):
    """None = not allowed to run now. Trading days: 7 PM-7 AM only. Non-trading days: all day."""
    hard = now + timedelta(minutes=max_minutes)
    today = now.date()
    if in_night_window(now):
        end_date = today if now.time() < WINDOW_END else today + timedelta(1)
        if not is_trading_day(end_date):
            return hard  # window flows into a non-trading day -> keep going
        return min(hard, datetime.combine(end_date, WINDOW_END, NPT))
    if not is_trading_day(today):
        return min(hard, datetime.combine(next_trading_day(today), WINDOW_END, NPT))
    return None


def pending_backfill(st: State):
    """Trading days from BACKFILL_START down to BACKFILL_END that still need scraping (newest first)."""
    empties = set(st.data["empty_dates"])
    out, d = [], BACKFILL_START
    while d >= BACKFILL_END:
        k = d.isoformat()
        if (is_trading_day(d) and not day_done(d) and k not in empties
                and st.data["failed"].get(k, 0) < MAX_FAILED_RUNS):
            out.append(d)
        d -= timedelta(1)
    return out


def process_day(sc: Scraper, st: State, d: date, deadline):
    """Returns 'saved' | 'empty' | 'deadline' | 'failed' | 'skipped'."""
    key = d.isoformat()
    for attempt in range(1, MAX_ATTEMPTS_PER_RUN + 1):
        try:
            res, _ = sc.scrape_day(d, deadline, st)
            if res == "empty":  # confirm with a fresh page load before trusting 'empty'
                res2, _ = sc.scrape_day(d, deadline, st)
                if res2 != "empty":
                    res = res2
            st.data["failed"].pop(key, None)
            st.save()
            return res
        except Deadline:
            log(f"{d}: time limit reached, progress saved - run again to continue")
            return "deadline"
        except Exception as e:  # noqa
            log(f"{d}: attempt {attempt}/{MAX_ATTEMPTS_PER_RUN} failed: {e}")
            sc.restart()
            _time.sleep(20)
    n = st.data["failed"].get(key, 0) + 1
    st.data["failed"][key] = n
    st.save()
    if n >= MAX_FAILED_RUNS:
        log(f"{d}: FAILED in {n} runs - skipped. Use --retry-failed (Manual run option) to try again.")
        return "skipped"
    return "failed"


def run_backfill(max_minutes, manual=False, retry_failed=False):
    now = datetime.now(NPT)
    st = State.load()
    if retry_failed:
        st.data["failed"] = {}
        st.save()
    if manual:
        deadline = now + timedelta(minutes=min(max_minutes, MANUAL_MAX_MINUTES))
    else:
        deadline = backfill_deadline(now, max_minutes)
        if deadline is None:
            log("Outside the backfill window (trading day, 7 AM-7 PM) - nothing to do.")
            return 0
    pending = pending_backfill(st)
    if not pending:
        st.data["backfill"]["done"] = True
        st.save()
        log(f"BACKFILL FINISHED: {BACKFILL_START} back to {BACKFILL_END} is complete.")
        return 0
    log(f"Backfill: {len(pending)} trading days left ({pending[0]} -> {pending[-1]}); "
        f"running until {deadline:%Y-%m-%d %H:%M} NPT")
    consecutive_fail = 0
    with Scraper() as sc:
        for d in pending:
            if datetime.now(NPT) >= deadline:
                break
            r = process_day(sc, st, d, deadline)
            if r == "deadline":
                break
            if r == "empty":
                st.data["empty_dates"].append(d.isoformat())
                st.save()
                log(f"{d}: no data on the website (holiday?) - recorded")
            consecutive_fail = consecutive_fail + 1 if r in ("failed", "skipped") else 0
            if consecutive_fail >= 3:
                log("3 days in a row failed - stopping this run (site layout changed or site down?)")
                return 1
    left = pending_backfill(st)
    if not left:
        st.data["backfill"]["done"] = True
        st.save()
        log("BACKFILL FINISHED.")
    else:
        log(f"{len(left)} trading days still to do - will continue in the next run.")
    return 0


def run_daily(max_minutes, manual=False, retry_failed=False):
    now = datetime.now(NPT)
    today = now.date()
    if today < DAILY_START:
        log("Daily scraping starts on " + DAILY_START.isoformat())
        return 0
    deadline = now + timedelta(minutes=min(max_minutes, MANUAL_MAX_MINUTES) if manual else max_minutes)
    st = State.load()
    if retry_failed:
        st.data["failed"] = {}
    empties = set(st.data["empty_dates"])
    pending, d = [], DAILY_START
    while d <= today:                      # includes catch-up for any missed earlier day
        if is_trading_day(d) and not day_done(d) and d.isoformat() not in empties:
            if d == today and now.time() < DAILY_EARLIEST:
                log(f"{d}: market data not final before {DAILY_EARLIEST:%H:%M} NPT - skipping today for now.")
            else:
                pending.append(d)
        d += timedelta(1)
    if not pending:
        log("Nothing to scrape right now.")
        return 0
    rc = 0
    with Scraper() as sc:
        for d in pending:
            tries = 0
            while True:
                r = process_day(sc, st, d, deadline)
                if r in ("saved", "skipped"):
                    break
                if r == "deadline":
                    return 1
                if r == "failed":
                    rc = 1
                    break
                tries += 1                 # r == 'empty'
                if d != today or tries >= 3:
                    st.data["empty_dates"].append(d.isoformat())
                    st.save()
                    log(f"{d}: no data on the website (holiday?) - recorded")
                    break
                if datetime.now(NPT) >= deadline:
                    return 1
                log(f"{d}: not published yet; retry in 10 min")
                _time.sleep(600)
    return rc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["backfill", "daily"])
    ap.add_argument("--max-minutes", type=int, default=330)
    ap.add_argument("--manual", action="store_true", help="ignore the time window; cap at 60 minutes")
    ap.add_argument("--retry-failed", action="store_true")
    a = ap.parse_args()
    fn = run_backfill if a.mode == "backfill" else run_daily
    sys.exit(fn(a.max_minutes, a.manual, a.retry_failed))
