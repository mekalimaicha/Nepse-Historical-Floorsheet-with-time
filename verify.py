"""Check (and optionally repair) every saved CSV.
   python -m src.verify          # report only
   python -m src.verify --fix    # rewrite files with a wrong Amount (e.g. 'Rs 6.7K')

Checks per file: header, Amount = Quantity x Rate in full digits, Date column = date in the file name,
Time looks like HH:MM:SS, and S.No runs 1,2,3... without gap or repeat (continuing across *_partNN files)."""
import csv
import os
import re
import sys
from collections import defaultdict

from .config import DATA_DIR, OUT_COLUMNS
from .parse import dec, money

TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")


def main():
    fix = "--fix" in sys.argv
    problems = 0
    next_sno = defaultdict(lambda: 1)           # per date: expected next S.No (parts continue the count)
    for f in sorted(DATA_DIR.rglob("*_Floorsheet*.csv")):
        day = f.name[:10]
        bad_amt, bad_date, bad_time, bad_seq, tmp = 0, 0, 0, 0, f.with_suffix(".fix")
        with open(f, newline="") as src, open(tmp, "w", newline="") as out:
            r, w = csv.reader(src), csv.writer(out, lineterminator="\n")
            header = next(r)
            if header != OUT_COLUMNS:
                print(f"{f}: unexpected header {header}")
                problems += 1
            w.writerow(header)
            for row in r:
                try:
                    amt = money(dec(row[5]) * dec(row[6]))
                except Exception:
                    amt = None
                if amt is None or row[7] != amt:
                    bad_amt += 1
                    if amt is not None:
                        row[7] = amt
                if row[8] != day:
                    bad_date += 1
                if not TIME_RE.match(row[9]):
                    bad_time += 1
                if row[0] != str(next_sno[day]):
                    bad_seq += 1
                    next_sno[day] = int(row[0]) if row[0].isdigit() else next_sno[day]
                next_sno[day] += 1
                w.writerow(row)
        if bad_amt and fix:
            os.replace(tmp, f)
        else:
            tmp.unlink()
        msgs = []
        if bad_amt:
            msgs.append(f"{bad_amt:,} wrong Amount values" + (" (fixed)" if fix else ""))
        if bad_date:
            msgs.append(f"{bad_date:,} rows whose Date differs from the file name")
        if bad_time:
            msgs.append(f"{bad_time:,} rows with an unreadable Time")
        if bad_seq:
            msgs.append(f"{bad_seq:,} S.No breaks (page skipped or repeated)")
        if msgs and not (bad_amt and fix and len(msgs) == 1):
            problems += 1
        print(f"{f.relative_to(DATA_DIR)}: {'OK' if not msgs else '; '.join(msgs)}")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
