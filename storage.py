import json
import math
import os
from datetime import date

from .config import DATA_DIR, MAX_FILE_BYTES, OUT_COLUMNS, PARTIAL_DIR, STATE_FILE


def day_done(d: date) -> bool:
    yd = DATA_DIR / str(d.year)
    return yd.exists() and any(yd.glob(f"{d.isoformat()}_Floorsheet*.csv"))


class State:
    def __init__(self, data):
        self.data = data
        self.data.setdefault("backfill", {"next_date": "2026-11-01", "empty_streak": 0, "done": False})
        self.data.setdefault("partial", {})
        self.data.setdefault("failed", {})
        self.data.setdefault("empty_dates", [])        # trading days confirmed to have no data (not in the holiday list)
        self.data.setdefault("holiday_overrides", [])  # listed holidays that DID have data (treated as trading days)
        self.data.setdefault("verified_holidays", [])  # listed holidays confirmed empty on the website

    @classmethod
    def load(cls):
        try:
            return cls(json.loads(STATE_FILE.read_text()))
        except FileNotFoundError:
            return cls({})

    def save(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True))
        os.replace(tmp, STATE_FILE)


def partial_path(d: date):
    PARTIAL_DIR.mkdir(parents=True, exist_ok=True)
    return PARTIAL_DIR / f"{d.isoformat()}.csv"


def partial_usable(part, meta) -> bool:
    """A partial file may only be resumed if it really holds the bytes the state says it holds.
    (If the cache restored an older/smaller file, truncating would pad it with NUL bytes.)"""
    try:
        return part.exists() and part.stat().st_size >= int(meta["bytes"])
    except (KeyError, TypeError, ValueError, OSError):
        return False


def check_sequence(part, expected_total: int):
    """Every row's S.No must be 1,2,3,...,N in order (no gap, no repeat). Returns (ok, message, rows, dup_contracts)."""
    rows, seen, dups = 0, set(), 0
    with open(part, "rb") as f:
        for line in f:
            rows += 1
            parts = line.split(b",", 2)
            try:
                sno = int(parts[0])
            except ValueError:
                return False, f"line {rows}: S.No {parts[0][:20]!r} is not a number", rows, dups
            if sno != rows:
                return False, f"line {rows}: S.No is {sno} (expected {rows}) - a page was skipped or repeated", rows, dups
            if len(parts) > 1:
                if parts[1] in seen:
                    dups += 1
                seen.add(parts[1])
    if rows != expected_total:
        return False, f"row count mismatch: scraped {rows:,} but site says {expected_total:,}", rows, dups
    return True, "ok", rows, dups


def finalize(d: date, expected_total: int):
    """Verify the rows (count, S.No 1..N in order) against the site's 'of N transactions' figure, then write
    data/YYYY/YYYY-MM-DD_Floorsheet.csv. Returns (ok, message)."""
    part = partial_path(d)
    ok, msg, rows, dups = check_sequence(part, expected_total)
    if not ok:
        return False, msg

    out_dir = DATA_DIR / str(d.year)
    out_dir.mkdir(parents=True, exist_ok=True)
    header = (",".join(OUT_COLUMNS) + "\n").encode()
    size = part.stat().st_size + len(header)
    parts = max(1, math.ceil(size / MAX_FILE_BYTES))
    per = math.ceil(rows / parts)
    names = ([f"{d}_Floorsheet.csv"] if parts == 1
             else [f"{d}_Floorsheet_part{i + 1:02d}.csv" for i in range(parts)])
    tmps = []
    with open(part, "rb") as src:
        for name in names:
            tmp = out_dir / (name + ".tmp")
            with open(tmp, "wb") as out:
                out.write(header)
                for _ in range(per):
                    line = src.readline()
                    if not line:
                        break
                    out.write(line)
            tmps.append((tmp, out_dir / name))
    for tmp, final in tmps:
        os.replace(tmp, final)
    part.unlink()
    note = "" if parts == 1 else f" (split into {parts} parts, GitHub 100 MB limit)"
    if dups:
        note += f" [note: {dups:,} repeated Contract No values on the site]"
    return True, f"saved {rows:,} rows{note}"
