"""Data-checking reports (written into state/ so they are committed with the data):

  state/non_trading_days.csv  every date from BACKFILL_END to today that is NOT a trading day, and why
                              (weekend / holiday list / no data on the website)
  state/coverage.txt          counts, and the list of trading days that still have no data file

   python -m src.report      # rebuild them by hand
"""
import csv
from datetime import datetime, timedelta

from .config import BACKFILL_END, NPT, REPORT_DIR
from .holidays import holiday_name
from .storage import State, day_done
from .trading_calendar import is_trading_day, is_trading_weekday


def write_reports(st: State):
    try:
        _write(st)
    except Exception as e:  # a report problem must never break a scrape
        print(f"(report not written: {e})")


def _write(st: State):
    today = datetime.now(NPT).date()
    empties = set(st.data["empty_dates"])
    overrides = set(st.data["holiday_overrides"])
    verified = set(st.data["verified_holidays"])
    failed = st.data["failed"]
    rows, have, missing = [], 0, []
    d = BACKFILL_END
    while d <= today:
        k = d.isoformat()
        if day_done(d):
            have += 1
        elif k in empties:
            rows.append((k, d.strftime("%A"), "no data on website (not in holiday list)"))
        elif not is_trading_weekday(d):
            rows.append((k, d.strftime("%A"), "weekend"))
        elif not is_trading_day(d) and k not in overrides:
            tag = "holiday list" + (" (confirmed empty on website)" if k in verified else "")
            rows.append((k, d.strftime("%A"), f"{tag}: {holiday_name(d)}"))
        elif d < today:
            missing.append(k)
        d += timedelta(1)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with open(REPORT_DIR / "non_trading_days.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "weekday", "reason"])
        w.writerows(rows)
    lines = [f"Coverage report {datetime.now(NPT):%Y-%m-%d %H:%M} NPT  (range {BACKFILL_END} .. {today})",
             f"days with a data file      : {have}",
             f"non-trading days listed    : {len(rows)}  (state/non_trading_days.csv)",
             f"trading days MISSING data  : {len(missing)}"]
    if missing:
        lines.append("  " + ", ".join(missing))
    if failed:
        lines.append("failed runs per day (parked until tomorrow when >= limit): "
                     + ", ".join(f"{k}:{v}" for k, v in sorted(failed.items())))
    (REPORT_DIR / "coverage.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write_reports(State.load())
    print((REPORT_DIR / "coverage.txt").read_text())
