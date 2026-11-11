"""Central settings for Nepse Historical Floorsheet with time."""
import os
from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = os.getenv("BASE_URL", "https://nepsealerts.com/floorsheet")
# Optional: if the site accepts the date in the URL, set e.g.
#   DATE_URL_TEMPLATE="https://nepsealerts.com/floorsheet?date={date}"
# (find the right pattern with `python -m src.probe`)
DATE_URL_TEMPLATE = os.getenv("DATE_URL_TEMPLATE", "")

NPT = ZoneInfo("Asia/Kathmandu")

BACKFILL_START = date(2026, 11, 2)          # newest day of the historical range (scraped first, going backwards)
BACKFILL_END = date(2026, 8, 21)            # oldest day - the website has no data before this
DAILY_START = date(2026, 11, 3)             # daily 3:30 PM job starts here (today)
DAILY_EARLIEST = time(15, 30)               # never scrape "today" before this (market must be closed)
MANUAL_MAX_MINUTES = 330                    # hard cap for a manually started run (the Manual workflow asks for minutes)
PARTIAL_FORMAT = 2                          # bump when the CSV row format changes (drops stale partial files)

PAGE_SIZE = 50                              # default only; the real size is read from "Showing 1 to 50 of N"
PAGE_DELAY = float(os.getenv("PAGE_DELAY", "0"))   # polite delay between pages (seconds); the scraper adds more by itself when the site struggles
BLOCK_ASSETS = os.getenv("BLOCK_ASSETS", "1") != "0"   # do not download images/fonts/media: faster, lighter on the site

# --- robustness knobs (the site is slow and sometimes drops a page) ---
LOAD_TIMEOUT = float(os.getenv("LOAD_TIMEOUT", "40"))    # seconds to wait for ONE page to show after a click
PAGE_RETRIES = int(os.getenv("PAGE_RETRIES", "8"))       # recovery tries for ONE page before the day attempt is abandoned
BACKOFF_SCALE = float(os.getenv("BACKOFF_SCALE", "1"))   # tests set this small; keep 1 in production
PACE_MAX = 2.0                                           # slowest automatic delay between pages (seconds)

DATA_DIR = ROOT / "data"
PARTIAL_DIR = ROOT / ".partial"
STATE_FILE = ROOT / "state" / "state.json"
DIAG_FILE = ROOT / "state" / "last_failure.txt"          # small text report of the last pagination failure (committed with state)
HOLIDAY_FILE = ROOT / "reference" / "holidays.csv"       # your 'Holidays List 2007-2027.csv'
REPORT_DIR = ROOT / "state"                              # non_trading_days.csv and coverage.txt are written here

OUT_COLUMNS = ["S.No", "Contract No", "Symbol", "Buyer", "Seller",
               "Quantity", "Rate (Rs)", "Amount (Rs)", "Date", "Time"]

WINDOW_START = time(19, 0)   # backfill window on trading days: 7 PM ...
WINDOW_END = time(7, 0)      # ... to 7 AM next morning (NPT)

MAX_FILE_BYTES = 95 * 1024 * 1024   # GitHub rejects files > 100 MB -> split above this
MAX_ATTEMPTS_PER_RUN = 5            # fresh-browser attempts per day in one run (progress is kept between attempts)
MAX_FAILED_RUNS = 3                 # a day that failed in this many runs is parked until tomorrow
