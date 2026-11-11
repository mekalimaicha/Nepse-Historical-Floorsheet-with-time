# Nepse Historical Floorsheet with time

Scrapes https://nepsealerts.com/floorsheet (with transaction **time**) into one CSV per trading date.

```
data/2026/2026-11-02_Floorsheet.csv
columns: S.No, Contract No, Symbol, Buyer, Seller, Quantity, Rate (Rs), Amount (Rs), Date, Time
```
* Date / Time cell on the site is split into `Date` (YYYY-MM-DD) and `Time` (24-hour HH:MM:SS).
* Numbers are saved without thousands separators. The site shows Amount as `Rs 6.7K`, so **Amount (Rs) = Quantity x Rate**, written in full digits with 2 decimals (e.g. `6700.00`).
* A day is saved **only if** the scraped rows are exactly `1..N` in order (no page skipped or repeated) and N equals the site's `Showing 1 to 50 of N transactions` figure. Otherwise it is discarded and retried.
* Files over 95 MB are split into `..._Floorsheet_part01.csv`, `_part02.csv` (GitHub rejects >100 MB).

## Schedules (Nepal time)
| Workflow | When |
|---|---|
| `backfill.yml` | History **2026-08-21 -> 2026-11-02** (newest first). Trading days 7 PM -> 7 AM; non-trading days all day; repeats until every trading day in the range is saved |
| `daily.yml` | 3:30 PM every trading day from 2026-11-03 (also catches up missed days; never scrapes today before 3:30 PM) |
| `manual.yml` | **Actions -> Manual run -> Run workflow.** Any time. You choose the *minutes* (default 120, max 330); it stops with progress saved, run it again to continue. Options: `retry_failed`, `verify_holidays` |

## Holidays (reference/holidays.csv)
`reference/holidays.csv` is your *Holidays List 2007-2027*. A date in it is a closed day: **it is not queried**
(backfill, daily and the 7 PM-7 AM window logic all use it). Keep the file up to date by editing/replacing it
(columns `S.No, Holiday Date (M/D/YYYY), Holiday Description`).

* Manual run with **verify_holidays** opens each listed holiday once: confirmed empty -> remembered as verified;
  if a listed holiday turns out to HAVE data it is scraped and treated as a trading day from then on.
* A trading weekday that is not in the list but has no data on the website is recorded as non-trading after
  **three** separate empty answers from fresh browser sessions (a slow site can never create a false holiday).

## Reports (committed in `state/`)
* `state/non_trading_days.csv` - every non-trading date and why (weekend / holiday list / no data on website)
* `state/coverage.txt` - days saved, and any trading day still missing
* `state/last_failure.txt` - what the page looked like at the last pagination failure (buttons, text, row numbers)
* `python -m src.verify` checks every CSV (Amount, Date, Time format, S.No continuity); `--fix` repairs old Amounts

## How the scraper stays reliable (the site is slow and drops pages)
* **Every page is verified** before it is kept: exact S.No range, row count and date column. A "Loading..." row,
  a half-drawn or empty table can never be taken for data.
* **A problem on one page is healed on that page**: wait, press a visible Retry button, click Next again, slow down
  (automatic pace 0-3 s/page), and only then reload the day and travel back to the page. The day is not abandoned.
* **Resume without hundreds of clicks** when the site allows it (page number in the address bar, or numbered page
  buttons); otherwise it steps Next/Previous with every step verified.
* Progress is checkpointed after every page; a partial file is only resumed if it really contains the saved bytes.
* A day that fails in 3 runs is parked and gets a fresh chance the next calendar day, so the automation never stalls.

## Setup
1. Create repo **Nepse-Historical-Floorsheet-with-time**, push these files.
2. Settings -> Actions -> General -> Workflow permissions -> **Read and write**.
3. (Optional) run the probe: `python -m src.probe`. If the site accepts a date in the URL, set `DATE_URL_TEMPLATE`.
4. Tests: `pip install pytest && pytest tests` (the `tests/test_mock_site.py` tests need Chromium:
   `playwright install chromium`; they run against a local fake of the website and prove the recovery logic).

## Limits to know
* Hosted GitHub runners stop at 6 h/job; progress is kept in the Actions cache and resumed.
  For multi-million-row days use a **self-hosted runner** (`runs-on: self-hosted`).
* Repository size: GitHub recommends < 5 GB; years of data will exceed this - plan Releases/LFS/external storage.
* Check the site's terms of use and keep `PAGE_DELAY` polite.
