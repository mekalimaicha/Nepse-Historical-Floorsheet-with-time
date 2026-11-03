import os, sys
from datetime import date, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from src.trading_calendar import is_trading_day
from src.parse import parse_date_time, parse_total, to_output_row
from src.runner import backfill_deadline
from src.config import NPT


def test_calendar():
    assert is_trading_day(date(2026, 11, 2))        # Mon
    assert not is_trading_day(date(2026, 11, 1))    # Sun (Mon-Fri era)
    assert is_trading_day(date(2026, 4, 6))         # first Mon-Fri day
    assert is_trading_day(date(2026, 4, 5))         # Sun, last Sun-Thu day
    assert not is_trading_day(date(2026, 4, 3))     # Fri in Sun-Thu era
    assert is_trading_day(date(2022, 5, 6))         # Fri, Mon-Fri introduced
    assert is_trading_day(date(2022, 6, 17))        # Fri in six-day period
    assert is_trading_day(date(2021, 1, 3)) and not is_trading_day(date(2021, 1, 1))


def test_parse():
    assert parse_date_time("2026-11-02\n14:55:03") == ("2026-11-02", "14:55:03")
    assert parse_date_time("Nov 2, 2026 02:05 PM") == ("2026-11-02", "14:05:00")
    assert parse_total("Showing 1 to 50 of 2,203,267.00 transactions") == 2203267
    idx = dict(sno=0, datetime=1, contract=2, symbol=3, buyer=4, seller=5, qty=6, rate=7, amount=8)
    r = to_output_row(["1", "2026-11-02\n11:00:01", "C1", "NABIL", "58", "45", "1,200", "Rs 500.50", "Rs 600.6K"], idx)
    assert r == ["1", "C1", "NABIL", "58", "45", "1200", "500.50", "600600.00", "2026-11-02", "11:00:01"]
    r = to_output_row(["2", "2026-11-02\n11:00:02", "C2", "X", "1", "2", "10", "Rs 670", "Rs 6.7K"], idx)
    assert r[5:8] == ["10", "670", "6700.00"]


def test_windows():
    f = lambda y, m, d, h: datetime(y, m, d, h, 0, tzinfo=NPT)
    assert backfill_deadline(f(2026, 11, 3, 12), 330) is None          # Tue noon: off
    assert backfill_deadline(f(2026, 11, 3, 20), 330) is not None      # Tue 8 PM: on
    assert backfill_deadline(f(2026, 11, 7, 12), 330) is not None      # Sat noon: non-trading, on
    assert backfill_deadline(f(2026, 11, 3, 3), 330) is not None       # Tue 3 AM: on


def test_finalize(tmp_path, monkeypatch):
    import src.storage as s
    monkeypatch.setattr(s, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(s, "PARTIAL_DIR", tmp_path / "p")
    d = date(2026, 11, 2)
    s.partial_path(d).write_text("".join(f"{i},C{i},X,1,2,3,4,5,2026-11-02,11:00:00\n" for i in range(1, 121)))
    ok, _ = s.finalize(d, 119)
    assert not ok
    ok, msg = s.finalize(d, 120)
    f = tmp_path / "data" / "2026" / "2026-11-02_Floorsheet.csv"
    assert ok and f.read_text().splitlines()[0] == "S.No,Contract No,Symbol,Buyer,Seller,Quantity,Rate (Rs),Amount (Rs),Date,Time"
    assert len(f.read_text().splitlines()) == 121


def test_backfill_range():
    from src.runner import pending_backfill
    from src.storage import State
    p = pending_backfill(State({}))
    assert p[0] == date(2026, 11, 2) and p[-1] == date(2026, 8, 21)
    assert len(p) == 52 and all(d.weekday() < 5 for d in p)
