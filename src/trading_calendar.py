"""NEPSE trading-day rules. Python weekday(): Mon=0 ... Sun=6."""
from datetime import date, timedelta

SUN_THU = {6, 0, 1, 2, 3}
MON_FRI = {0, 1, 2, 3, 4}
SUN_FRI = {6, 0, 1, 2, 3, 4}

# ASSUMPTION: you gave the start (15 Jun 2022) of the six-day period but not its end.
# Change this date if you know the real end. Until then Sun-Fri is used.
SIX_DAY_END = date(2022, 7, 16)

PERIODS = [
    (date.min,                      date(2022, 5, 5),   SUN_THU),
    (date(2022, 5, 6),              date(2022, 6, 14),  MON_FRI),
    (date(2022, 6, 15),             SIX_DAY_END,        SUN_FRI),
    (SIX_DAY_END + timedelta(1),    date(2026, 4, 5),   SUN_THU),
    (date(2026, 4, 6),              date.max,           MON_FRI),
]


def is_trading_day(d: date) -> bool:
    for start, end, days in PERIODS:
        if start <= d <= end:
            return d.weekday() in days
    return False


def next_trading_day(d: date) -> date:
    d += timedelta(1)
    while not is_trading_day(d):
        d += timedelta(1)
    return d
