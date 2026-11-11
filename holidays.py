"""NEPSE holiday list (reference/holidays.csv = your 'Holidays List 2007-2027.csv').

Columns: S.No, Holiday Date (M/D/YYYY), Holiday Description.
A date in this list is a day the market was closed: the scraper does not query it."""
import csv
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

from .config import HOLIDAY_FILE


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    out: dict = {}
    try:
        f = open(path, newline="", encoding="utf-8-sig")
    except FileNotFoundError:
        return out
    with f:
        for row in csv.DictReader(f):
            raw = (row.get("Holiday Date") or "").strip()
            try:
                d = datetime.strptime(raw, "%m/%d/%Y").date()
            except ValueError:
                continue
            name = " ".join((row.get("Holiday Description") or "").split())
            if d in out:
                if name and name.lower() not in out[d].lower():
                    out[d] = f"{out[d]} / {name}"
            else:
                out[d] = name
    return out


def load_holidays(path=None) -> dict:
    """{date: description}. Empty if the file is missing (the scraper then just queries every weekday)."""
    return _load(str(path or HOLIDAY_FILE))


def is_holiday(d: date) -> bool:
    return d in load_holidays()


def holiday_name(d: date) -> str:
    return load_holidays().get(d, "")
