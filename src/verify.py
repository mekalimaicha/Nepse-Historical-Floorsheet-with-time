"""Check (and optionally repair) every saved CSV: Amount must equal Quantity x Rate in full digits.
   python -m src.verify          # report only
   python -m src.verify --fix    # rewrite files with wrong Amount (e.g. 'Rs 6.7K')"""
import csv
import os
import sys

from .config import DATA_DIR, OUT_COLUMNS
from .parse import dec, money


def main():
    fix = "--fix" in sys.argv
    problems = 0
    for f in sorted(DATA_DIR.rglob("*_Floorsheet*.csv")):
        bad, tmp = 0, f.with_suffix(".fix")
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
                    bad += 1
                    if amt is not None:
                        row[7] = amt
                w.writerow(row)
        if bad and fix:
            os.replace(tmp, f)
        else:
            tmp.unlink()
        if bad:
            problems += 1
        print(f"{f.relative_to(DATA_DIR)}: {'OK' if not bad else f'{bad:,} wrong Amount values' + (' (fixed)' if fix else '')}")
    sys.exit(1 if problems and not fix else 0)


if __name__ == "__main__":
    main()
