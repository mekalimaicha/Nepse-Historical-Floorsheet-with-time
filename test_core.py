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
    assert all(d.weekday() < 5 for d in p)
    # 52 Mon-Fri days in the range minus the 9 weekday holidays of your list (Aug 28, Sep 4, 8, 21, 25, Oct 19-22)
    assert len(p) == 43
    assert not {date(2026, 10, 19), date(2026, 10, 22), date(2026, 9, 21)} & set(p)


def test_holiday_list():
    from src.holidays import holiday_name, is_holiday, load_holidays
    assert len(load_holidays()) == 2288
    assert is_holiday(date(2026, 10, 20)) and holiday_name(date(2026, 10, 20)) == "MahaNawami"
    assert "Lockdown" in holiday_name(date(2020, 4, 13)) and "/" in holiday_name(date(2020, 4, 13))   # two reasons joined
    assert not is_holiday(date(2026, 11, 2))
    assert not is_trading_day(date(2026, 10, 20))


def test_holiday_overrides_and_verify_mode():
    from src.runner import pending_backfill, trading
    from src.storage import State
    st = State({})
    assert not trading(date(2026, 10, 20), st)
    st.data["holiday_overrides"].append("2026-10-20")
    assert trading(date(2026, 10, 20), st)
    st2 = State({})
    p = pending_backfill(st2, verify_holidays=True)
    assert date(2026, 10, 20) in p and len(p) == 52          # holidays included once
    st2.data["verified_holidays"] += ["2026-10-20", "2026-10-19"]
    assert date(2026, 10, 20) not in pending_backfill(st2, verify_holidays=True)


def test_footer():
    from src.parse import parse_footer
    assert parse_footer("Showing 51 to 100 of 38,861 transactions") == (51, 100, 38861)
    assert parse_footer("nothing here") is None


def test_sequence_check_and_safe_resume(tmp_path):
    import src.storage as s
    p = tmp_path / "x.csv"
    p.write_text("1,A\n2,B\n3,C\n")
    assert s.check_sequence(p, 3)[0]
    ok, msg, *_ = s.check_sequence(p, 4)
    assert not ok and "mismatch" in msg
    p.write_text("1,A\n3,C\n")
    ok, msg, *_ = s.check_sequence(p, 2)
    assert not ok and "skipped" in msg
    p.write_text("1,A\n2,B\n")
    assert s.partial_usable(p, {"bytes": 8}) and not s.partial_usable(p, {"bytes": 9})   # cache gave an older file


def test_report(tmp_path, monkeypatch):
    import src.report as r
    import src.storage as s
    monkeypatch.setattr(r, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(s, "DATA_DIR", tmp_path / "data")
    st = s.State({})
    st.data["empty_dates"].append("2026-10-23")
    r.write_reports(st)
    text = (tmp_path / "non_trading_days.csv").read_text()
    assert "2026-10-20,Tuesday,holiday list: MahaNawami" in text
    assert "2026-10-23,Friday,no data on website" in text
    assert "2026-10-24,Saturday,weekend" in text
