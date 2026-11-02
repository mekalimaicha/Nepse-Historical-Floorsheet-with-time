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


def finalize(d: date, expected_total: int):
    """Verify the row count against the site's 'of N transactions' figure, then write
    data/YYYY/YYYY-MM-DD_Floorsheet.csv. Returns (ok, message)."""
    part = partial_path(d)
    rows, last = 0, b""
    with open(part, "rb") as f:
        for line in f:
            rows += 1
            last = line
    if rows != expected_total:
        return False, f"row count mismatch: scraped {rows:,} but site says {expected_total:,}"
    last_sno = last.split(b",", 1)[0].decode().strip()
    if last_sno and last_sno.isdigit() and int(last_sno) != expected_total:
        return False, f"last S.No {last_sno} != total {expected_total:,}"

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
    return True, f"saved {rows:,} rows{note}"
