"""Central settings for Nepse Historical Floorsheet with time."""
import os
from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://nepsealerts.com/floorsheet"
# Optional: if the site accepts the date in the URL, set e.g.
#   DATE_URL_TEMPLATE="https://nepsealerts.com/floorsheet?date={date}"
# (find the right pattern with `python -m src.probe`)
DATE_URL_TEMPLATE = os.getenv("DATE_URL_TEMPLATE", "")

NPT = ZoneInfo("Asia/Kathmandu")

BACKFILL_START = date(2026, 11, 1)          # go backwards from here
EXTRA_BACKFILL_DATES = [date(2026, 11, 2)]  # gap between backfill start and daily start
DAILY_START = date(2026, 11, 3)             # daily 3:30 PM job starts here
EMPTY_DAYS_LIMIT = 30                       # stop after 30 consecutive trading days with no data

PAGE_SIZE = 50
PAGE_DELAY = float(os.getenv("PAGE_DELAY", "0"))   # polite delay between pages (seconds)

DATA_DIR = ROOT / "data"
PARTIAL_DIR = ROOT / ".partial"
STATE_FILE = ROOT / "state" / "state.json"

OUT_COLUMNS = ["S.No", "Contract No", "Symbol", "Buyer", "Seller",
               "Quantity", "Rate (Rs)", "Amount (Rs)", "Date", "Time"]

WINDOW_START = time(19, 0)   # backfill window on trading days: 7 PM ...
WINDOW_END = time(7, 0)      # ... to 7 AM next morning (NPT)

MAX_FILE_BYTES = 95 * 1024 * 1024   # GitHub rejects files > 100 MB -> split above this
MAX_ATTEMPTS_PER_RUN = 3
MAX_FAILED_RUNS = 3
