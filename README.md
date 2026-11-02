# Nepse Historical Floorsheet with time

Scrapes https://nepsealerts.com/floorsheet (with transaction **time**) into one CSV per trading date.

```
data/2026/2026-11-02_Floorsheet.csv
columns: S.No, Contract No, Symbol, Buyer, Seller, Quantity, Rate (Rs), Amount (Rs), Date, Time
```
* Date / Time cell on the site is split into `Date` (YYYY-MM-DD) and `Time` (24-hour HH:MM:SS).
* Numbers are saved without thousands separators.
* A day is saved **only if** the scraped row count equals the site's `Showing 1 to 50 of N transactions` figure.
  A mismatch is discarded and retried (never saved as a final file).
* Files over 95 MB are split into `..._Floorsheet_part01.csv`, `_part02.csv` (GitHub rejects >100 MB).

## Schedules (Nepal time)
| Workflow | When |
|---|---|
| `backfill.yml` | Trading days 7 PM -> 7 AM; non-trading days all day; repeats until finished |
| `daily.yml` | 3:30 PM every trading day, from 2026-11-03 (also catches up missed days) |

Backfill goes from 2026-11-01 backwards (2026-11-02 is fetched first so there is no gap before the daily
job starts) and finishes after **30 consecutive trading days with no data**. State: `state/state.json`.
Trading days: `src/trading_calendar.py` (edit `SIX_DAY_END` once you know when the Sun-Fri period ended).

## Setup
1. Create repo **Nepse-Historical-Floorsheet-with-time** (GitHub turns spaces into hyphens), push these files.
2. Settings -> Actions -> General -> Workflow permissions -> **Read and write**.
3. **First, run the probe** (local machine or Actions): `pip install -r requirements.txt && playwright install chromium && python -m src.probe`
   It shows the date input/buttons and the XHR calls. If the site accepts a date in the URL, set
   `DATE_URL_TEMPLATE` (env / repo variable), e.g. `https://nepsealerts.com/floorsheet?date={date}`.
4. Test one day locally: `python -m src.runner daily` (or run a workflow via *Run workflow*).
5. Tests: `pytest tests`

## Limits to know
* Hosted GitHub runners stop at 6 h/job; the scraper checkpoints each page and resumes (progress is kept in the Actions cache).
  For multi-million-row days use a **self-hosted runner** (`runs-on: self-hosted`).
* Repository size: GitHub recommends < 5 GB; years of data will exceed this - plan Releases/LFS/external storage.
* Check the site's terms of use / robots rules and keep `PAGE_DELAY` polite.
