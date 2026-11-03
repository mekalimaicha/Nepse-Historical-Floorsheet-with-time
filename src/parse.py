import re
from datetime import date, datetime
from decimal import Decimal

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y")
_DATE_RE = re.compile(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}|[A-Za-z]{3,9}\.? \d{1,2},? \d{4}|\d{1,2} [A-Za-z]{3,9} \d{4})")
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?")
TOTAL_RE = re.compile(r"Showing\s+([\d,]+)\s+to\s+([\d,]+)\s+of\s+([\d,]+(?:\.\d+)?)\s+transactions", re.I)


def parse_date_time(cell: str):
    """'2026-11-02\\n14:55:03' -> ('2026-11-02', '14:55:03'). Time is 24-hour."""
    text = " ".join(cell.split())
    dm, tm = _DATE_RE.search(text), _TIME_RE.search(text)
    if not dm or not tm:
        raise ValueError(f"Cannot parse date/time cell: {cell!r}")
    raw = dm.group(1).replace(".", "").replace(",", ", ").replace("  ", " ")
    d = None
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(raw.replace(", ,", ","), fmt).date()
            break
        except ValueError:
            continue
    if d is None:
        raise ValueError(f"Unknown date format: {dm.group(1)!r}")
    h, m, s, ap = int(tm.group(1)), int(tm.group(2)), int(tm.group(3) or 0), tm.group(4)
    if ap:
        ap = ap.lower()
        h = h % 12 + (12 if ap == "pm" else 0)
    return d.isoformat(), f"{h:02d}:{m:02d}:{s:02d}"


def dec(s: str) -> Decimal:
    """'Rs 1,023.50' -> Decimal('1023.50'). Refuses abbreviated numbers such as '6.7K' (they are lossy)."""
    t = s.replace("Rs", "").replace("रु", "").replace(",", "").strip()
    if re.search(r"[A-Za-z]", t):
        raise ValueError(f"Abbreviated number {s!r} - cannot be used for exact calculation")
    return Decimal(t)


def money(x: Decimal) -> str:
    return f"{x.quantize(Decimal('0.01')):.2f}"


def parse_total(text: str):
    m = TOTAL_RE.search(text)
    return int(float(m.group(3).replace(",", ""))) if m else None


def to_output_row(cells, idx):
    """cells: raw cell texts; idx: dict header-name -> column index."""
    d, t = parse_date_time(cells[idx["datetime"]])
    qty, rate = dec(cells[idx["qty"]]), dec(cells[idx["rate"]])
    # The site shows Amount abbreviated ("Rs 6.7K"), so it is ignored and recomputed = Quantity x Rate.
    return [cells[idx["sno"]].strip(), cells[idx["contract"]].strip(), cells[idx["symbol"]].strip(),
            cells[idx["buyer"]].strip(), cells[idx["seller"]].strip(),
            format(qty, "f"), format(rate, "f"), money(qty * rate), d, t]
