"""Entry point:
   python -m src.runner backfill|daily [--max-minutes N] [--manual] [--retry-failed] [--verify-holidays]
--manual: ignore the 7 PM-7 AM window (the Manual workflow passes how many minutes it may run).
--verify-holidays: ALSO query the days your holiday list marks as closed, once, to prove they really have no data
                   (a listed holiday that does have data is scraped and from then on treated as a trading day)."""
import argparse
import sys
import time as _time
from datetime import date, datetime, timedelta

from .config import (BACKFILL_END, BACKFILL_START, DAILY_EARLIEST, DAILY_START, MANUAL_MAX_MINUTES,
                     MAX_ATTEMPTS_PER_RUN, MAX_FAILED_RUNS, NPT, WINDOW_END, WINDOW_START)
from .holidays import holiday_name
from .report import write_reports
from .scraper import Deadline, Scraper, log
from .storage import State, day_done
from .trading_calendar import is_trading_day, is_trading_weekday, next_trading_day


def in_night_window(t: datetime) -> bool:
    return t.time() >= WINDOW_START or t.time() < WINDOW_END


def backfill_deadline(now: datetime, max_minutes: int):
    """None = not allowed to run now. Trading days: 7 PM-7 AM only. Non-trading days (weekend/holiday): all day."""
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


def trading(d: date, st: State) -> bool:
    """Trading day = weekday rule + not in the holiday list (unless the website proved the 'holiday' had data)."""
    return is_trading_day(d) or d.isoformat() in st.data["holiday_overrides"]


def is_listed_holiday_weekday(d: date) -> bool:
    return is_trading_weekday(d) and not is_trading_day(d)


def pending_backfill(st: State, verify_holidays: bool = False):
    """Days from BACKFILL_START down to BACKFILL_END that still need scraping (newest first)."""
    empties = set(st.data["empty_dates"])
    verified = set(st.data["verified_holidays"])
    out, d = [], BACKFILL_START
    while d >= BACKFILL_END:
        k = d.isoformat()
        wanted = trading(d, st) or (verify_holidays and is_listed_holiday_weekday(d) and k not in verified)
        if (wanted and not day_done(d) and k not in empties
                and st.data["failed"].get(k, 0) < MAX_FAILED_RUNS):
            out.append(d)
        d -= timedelta(1)
    return out


def reset_failed_daily(st: State, now: datetime):
    """A day parked after MAX_FAILED_RUNS failed runs gets a fresh chance each calendar day (so automation never stalls)."""
    today = now.date().isoformat()
    if st.data.get("failed_reset_on") != today:
        if st.data["failed"]:
            log(f"New day: giving {len(st.data['failed'])} previously failed day(s) a fresh chance.")
        st.data["failed"] = {}
        st.data["failed_reset_on"] = today
        st.save()


def process_day(sc: Scraper, st: State, d: date, deadline):
    """Returns 'saved' | 'empty' | 'deadline' | 'failed' | 'skipped'."""
    key = d.isoformat()
    for attempt in range(1, MAX_ATTEMPTS_PER_RUN + 1):
        try:
            res, _ = sc.scrape_day(d, deadline, st)
            if res == "empty":
                # Never trust one 'empty' answer from a slow site: confirm with two more FRESH browser sessions.
                for i in (1, 2):
                    log(f"{d}: no data shown - confirming with a fresh browser ({i}/2)")
                    _time.sleep(20)
                    sc.restart()
                    res2, _ = sc.scrape_day(d, deadline, st)
                    if res2 != "empty":
                        res = res2
                        break
            st.data["failed"].pop(key, None)
            st.save()
            return res
        except Deadline:
            log(f"{d}: time limit reached, progress saved - run again to continue")
            return "deadline"
        except Exception as e:  # noqa
            log(f"{d}: attempt {attempt}/{MAX_ATTEMPTS_PER_RUN} failed: {str(e)[:300]}")
            st.save()                       # keep the page-by-page progress
            sc.restart()
            _time.sleep(min(60, 15 * attempt))
    n = st.data["failed"].get(key, 0) + 1
    st.data["failed"][key] = n
    st.save()
    if n >= MAX_FAILED_RUNS:
        log(f"{d}: FAILED in {n} runs - parked until tomorrow (or use the Manual run option 'retry failed').")
        return "skipped"
    return "failed"


def record_result(st: State, d: date, r: str):
    key = d.isoformat()
    listed = is_listed_holiday_weekday(d) and key not in st.data["holiday_overrides"]
    if r == "empty":
        if listed:
            if key not in st.data["verified_holidays"]:
                st.data["verified_holidays"].append(key)
            log(f"{d}: no data, as the holiday list says ({holiday_name(d)}) - confirmed")
        else:
            if key not in st.data["empty_dates"]:
                st.data["empty_dates"].append(key)
            log(f"{d}: no data on the website - recorded as a non-trading day")
        st.save()
    elif r == "saved" and listed:
        st.data["holiday_overrides"].append(key)
        st.save()
        log(f"{d}: this day is in the holiday list but HAS data - now treated as a trading day")


def run_backfill(max_minutes, manual=False, retry_failed=False, verify_holidays=False):
    now = datetime.now(NPT)
    st = State.load()
    reset_failed_daily(st, now)
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
    pending = pending_backfill(st, verify_holidays)
    skipped_h = [d for d in (BACKFILL_START - timedelta(i) for i in range((BACKFILL_START - BACKFILL_END).days + 1))
                 if is_listed_holiday_weekday(d)]
    if skipped_h and not verify_holidays:
        log(f"Holiday list: skipping {len(skipped_h)} closed weekdays in range: "
            + ", ".join(f"{d:%m-%d} {holiday_name(d)}" for d in sorted(skipped_h)))
    if not pending:
        st.data["backfill"]["done"] = True
        st.save()
        write_reports(st)
        log(f"BACKFILL FINISHED: {BACKFILL_START} back to {BACKFILL_END} is complete.")
        return 0
    log(f"Backfill: {len(pending)} days left ({pending[0]} -> {pending[-1]}); "
        f"running until {deadline:%Y-%m-%d %H:%M} NPT")
    consecutive_fail = 0
    rc = 0
    with Scraper() as sc:
        for d in pending:
            if datetime.now(NPT) >= deadline:
                break
            r = process_day(sc, st, d, deadline)
            if r == "deadline":
                break
            record_result(st, d, r)
            consecutive_fail = consecutive_fail + 1 if r in ("failed", "skipped") else 0
            if consecutive_fail >= 3:
                log("3 days in a row failed - stopping this run (see state/last_failure.txt for what the page looked like)")
                rc = 1
                break
    left = pending_backfill(st, verify_holidays)
    if not left and rc == 0:
        st.data["backfill"]["done"] = True
        st.save()
        log("BACKFILL FINISHED.")
    elif rc == 0:
        log(f"{len(left)} days still to do - will continue in the next run.")
    write_reports(st)
    return rc


def run_daily(max_minutes, manual=False, retry_failed=False, verify_holidays=False):
    now = datetime.now(NPT)
    today = now.date()
    if today < DAILY_START:
        log("Daily scraping starts on " + DAILY_START.isoformat())
        return 0
    deadline = now + timedelta(minutes=min(max_minutes, MANUAL_MAX_MINUTES) if manual else max_minutes)
    st = State.load()
    reset_failed_daily(st, now)
    if retry_failed:
        st.data["failed"] = {}
    empties = set(st.data["empty_dates"])
    pending, d = [], DAILY_START
    while d <= today:                      # includes catch-up for any missed earlier day
        if trading(d, st) and not day_done(d) and d.isoformat() not in empties:
            if d == today and now.time() < DAILY_EARLIEST:
                log(f"{d}: market data not final before {DAILY_EARLIEST:%H:%M} NPT - skipping today for now.")
            else:
                pending.append(d)
        elif is_listed_holiday_weekday(d):
            log(f"{d}: holiday ({holiday_name(d)}) - skipped")
        d += timedelta(1)
    if not pending:
        log("Nothing to scrape right now.")
        write_reports(st)
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
                    write_reports(st)
                    return 1
                if r == "failed":
                    rc = 1
                    break
                tries += 1                 # r == 'empty'
                if d != today or tries >= 3:
                    record_result(st, d, "empty")
                    break
                if datetime.now(NPT) >= deadline:
                    write_reports(st)
                    return 1
                log(f"{d}: not published yet; retry in 10 min")
                _time.sleep(600)
    write_reports(st)
    return rc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["backfill", "daily"])
    ap.add_argument("--max-minutes", type=int, default=330)
    ap.add_argument("--manual", action="store_true", help="ignore the time window; cap at MANUAL_MAX_MINUTES")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--verify-holidays", action="store_true",
                    help="also query days the holiday list marks as closed, once, to confirm they have no data")
    a = ap.parse_args()
    fn = run_backfill if a.mode == "backfill" else run_daily
    sys.exit(fn(a.max_minutes, a.manual, a.retry_failed, a.verify_holidays))
