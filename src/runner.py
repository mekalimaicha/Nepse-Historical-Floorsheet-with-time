"""Entry point:  python -m src.runner backfill|daily [--max-minutes N]"""
import argparse
import sys
import time as _time
from datetime import date, datetime, time, timedelta

from .config import (BACKFILL_START, DAILY_START, EMPTY_DAYS_LIMIT, EXTRA_BACKFILL_DATES,
                     MAX_ATTEMPTS_PER_RUN, MAX_FAILED_RUNS, NPT, WINDOW_END, WINDOW_START)
from .scraper import Deadline, ScrapeError, Scraper, log
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
            log(f"{d}: deadline reached, progress saved - will resume next run")
            return "deadline"
        except Exception as e:  # noqa
            log(f"{d}: attempt {attempt}/{MAX_ATTEMPTS_PER_RUN} failed: {e}")
            sc.restart()
            _time.sleep(20)
    n = st.data["failed"].get(key, 0) + 1
    st.data["failed"][key] = n
    st.save()
    return "skipped" if n >= MAX_FAILED_RUNS else "failed"


def run_backfill(max_minutes):
    now = datetime.now(NPT)
    deadline = backfill_deadline(now, max_minutes)
    if deadline is None:
        log("Outside the backfill window (trading day, 7 AM-7 PM) - nothing to do.")
        return 0
    st = State.load()
    bf = st.data["backfill"]
    if bf.get("done"):
        log("Backfill already finished.")
        return 0
    log(f"Backfill running until {deadline:%Y-%m-%d %H:%M} NPT")
    with Scraper() as sc:
        for d in EXTRA_BACKFILL_DATES:
            if is_trading_day(d) and not day_done(d):
                if process_day(sc, st, d, deadline) in ("deadline", "failed"):
                    return 0 if datetime.now(NPT) >= deadline else 1
        d = date.fromisoformat(bf["next_date"])
        streak = bf.get("empty_streak", 0)
        while streak < EMPTY_DAYS_LIMIT:
            if datetime.now(NPT) >= deadline:
                break
            if is_trading_day(d) and not day_done(d):
                r = process_day(sc, st, d, deadline)
                if r == "deadline":
                    break
                if r == "failed":
                    return 1
                streak = streak + 1 if r == "empty" else (0 if r == "saved" else streak)
                if r == "empty":
                    log(f"{d}: no data (empty streak {streak}/{EMPTY_DAYS_LIMIT})")
            elif is_trading_day(d):
                streak = 0  # already stored
            d -= timedelta(1)
            bf.update(next_date=d.isoformat(), empty_streak=streak)
            st.save()
        if streak >= EMPTY_DAYS_LIMIT:
            bf["done"] = True
            st.save()
            log(f"BACKFILL FINISHED: {EMPTY_DAYS_LIMIT} consecutive trading days without data.")
    return 0


def run_daily(max_minutes):
    now = datetime.now(NPT)
    today = now.date()
    if today < DAILY_START or not is_trading_day(today):
        log("Not a daily-scrape trading day.")
        return 0
    deadline = now + timedelta(minutes=max_minutes)
    st = State.load()
    pending, d = [], DAILY_START
    while d <= today:                      # includes catch-up for any missed earlier day
        if is_trading_day(d) and not day_done(d):
            pending.append(d)
        d += timedelta(1)
    rc = 0
    with Scraper() as sc:
        for d in pending:
            for tryno in range(6):         # data may not be published yet: wait 10 min and retry
                r = process_day(sc, st, d, deadline)
                if r == "saved" or r == "skipped":
                    break
                if r == "empty" and d != today:
                    log(f"{d}: confirmed empty (holiday?)")
                    break
                if r == "deadline" or datetime.now(NPT) >= deadline:
                    return 1
                log(f"{d}: not ready ({r}); retry in 10 min")
                _time.sleep(600)
            else:
                rc = 1
    return rc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["backfill", "daily"])
    ap.add_argument("--max-minutes", type=int, default=330)
    a = ap.parse_args()
    sys.exit(run_backfill(a.max_minutes) if a.mode == "backfill" else run_daily(a.max_minutes))
