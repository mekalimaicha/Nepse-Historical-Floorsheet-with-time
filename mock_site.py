"""Local stand-in for nepsealerts.com/floorsheet used only for testing.

Mimics what the scraper relies on (as seen in the working code):
  input[type=date] + "Search" button, table thead/tbody (9 columns), footer
  "Showing X to Y of N transactions", "Next"/"Previous" buttons, 50 rows per page.
Failure switches (module-level CONFIG) reproduce the problems seen in the user's log.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOTALS = {"2026-10-15": 38861, "2026-10-16": 620, "2026-10-14": 130, "2026-10-13": 3000, "2026-10-09": 50, "2026-10-08": 51}   # others -> empty day
SIZE = 50

CONFIG = {
    "latency": 0.05,            # seconds per API call
    "skeleton": True,           # while loading: table shows a 'Loading...' row (first cell changes!)
    "fail_once": set(),         # page numbers whose FIRST api call returns HTTP 500 (error card, no pagination)
    "fail_until_reload": set(), # pages that fail every time until the browser reloads the document
    "swallow": set(),           # pages whose first Next click is ignored by the site
    "numbers": False,           # show windowed page-number buttons
    "urlsync": False,           # keep ?date=&page= in the address bar and honour it on load
    "wrong_date_on_load": False,
    "serve_other_date": False,  # the site ignores the requested date and serves 2026-10-01 rows
    "empty_glitch_last": False, # last page: first api answer has rows=[] (renders an empty table)
}
STATE = {"calls": 0, "seen": set(), "reload_epoch": 0, "log": []}
LOCK = threading.Lock()


def rows_for(date, page):
    total = TOTALS.get(date, 0)
    out = []
    for i in range((page - 1) * SIZE + 1, min(page * SIZE, total) + 1):
        h = 10 + (i * 5 // max(total, 1))
        out.append([str(i), f"{date}\n{h:02d}:{i % 60:02d}:{(i * 7) % 60:02d}", f"{date.replace('-', '')}0100{i:06d}",
                    "NABIL", str(i % 90 + 1), str(i % 80 + 1), f"{(i % 500) + 1:,}", f"Rs {(i % 900) + 100}.50",
                    f"Rs {((i % 500) + 1) * ((i % 900) + 100) / 1000:.1f}K"])
    return total, out


HTML = r"""<!doctype html><html><head><meta charset="utf-8"><title>Floorsheet</title></head><body>
<h1>Floorsheet</h1>
<div><input type="date" id="d"> <button id="s">Search</button></div>
<table><thead><tr><th>S.No</th><th>Date / Time</th><th>Contract No</th><th>Symbol</th><th>Buyer</th><th>Seller</th>
<th>Quantity</th><th>Rate (Rs)</th><th>Amount (Rs)</th></tr></thead><tbody id="tb"></tbody></table>
<div id="foot"></div><div id="pg"></div>
<script>
const CFG = __CFG__;
let date = null, page = 1, total = 0, rows = [], loading = false, err = false, epoch = __EPOCH__;
const SIZE = 50;
function pages(){ return Math.max(1, Math.ceil(total / SIZE)); }
function nums(){
  const n = pages(), s = new Set([1, n]);
  for (let k = page - 2; k <= page + 2; k++) if (k >= 1 && k <= n) s.add(k);
  return [...s].sort((a, b) => a - b);
}
function render(){
  const tb = document.getElementById('tb');
  if (loading && CFG.skeleton) tb.innerHTML = '<tr><td colspan="9">Loading...</td></tr>';
  else if (err || loading) tb.innerHTML = '';
  else if (!rows.length) tb.innerHTML = '<tr><td colspan="9">No data</td></tr>';
  else tb.innerHTML = rows.map(r => '<tr>' + r.map(c => '<td>' + c.replace('\n', '<br>') + '</td>').join('') + '</tr>').join('');
  const f = document.getElementById('foot'), pg = document.getElementById('pg');
  if (err) { f.textContent = ''; pg.innerHTML = '<div>Failed to load <button id="retry">Retry</button></div>';
             document.getElementById('retry').onclick = () => load(page); return; }
  if (!date) { f.textContent = ''; pg.innerHTML = ''; return; }
  const from = total ? (page - 1) * SIZE + 1 : 0, to = Math.min(page * SIZE, total);
  f.textContent = loading ? '' : `Showing ${from} to ${to} of ${total.toLocaleString('en-US')} transactions`;
  let h = '<button id="prev"' + (page <= 1 ? ' disabled' : '') + '>Previous</button> ';
  if (CFG.numbers) for (const k of nums()) h += `<button class="pn">${k}</button> `;
  h += '<button id="next"' + (page >= pages() ? ' disabled' : '') + '>Next</button>';
  pg.innerHTML = h;
  document.getElementById('prev').onclick = () => go(page - 1, true);
  document.getElementById('next').onclick = () => go(page + 1, true);
  document.querySelectorAll('.pn').forEach(b => b.onclick = () => go(parseInt(b.textContent), true));
}
async function go(p, click){
  if (click && CFG.swallow.includes(p) && !window.__sw) { window.__sw = {}; }
  if (click && CFG.swallow.includes(p) && !window.__sw[p]) { window.__sw[p] = 1; return; }
  await load(p);
}
async function load(p){
  loading = true; err = false; render();
  try {
    const r = await fetch(`/api/fs?date=${date}&page=${p}&epoch=${epoch}`);
    if (!r.ok) throw new Error('http ' + r.status);
    const j = await r.json(); rows = j.rows; total = j.total; page = p;
  } catch (e) { err = true; }
  loading = false; render();
  if (CFG.urlsync && !err) history.replaceState(null, '', `?date=${date}&page=${page}`);
}
document.getElementById('s').onclick = () => { date = document.getElementById('d').value; page = 1; load(1); };
const q = new URLSearchParams(location.search);
if (CFG.urlsync && q.get('date')) { date = q.get('date'); document.getElementById('d').value = date; load(parseInt(q.get('page') || '1')); }
else if (CFG.wrong_date_on_load) { }
render();
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/floorsheet":
            with LOCK:
                STATE["reload_epoch"] += 1      # each document load = new epoch (a 'reload' heals stuck pages)
                ep = STATE["reload_epoch"]
            cfg = {k: (sorted(v) if isinstance(v, set) else v) for k, v in CONFIG.items()}
            self._send(200, HTML.replace("__CFG__", json.dumps(cfg)).replace("__EPOCH__", str(ep)), "text/html")
        elif u.path == "/api/fs":
            import time
            time.sleep(CONFIG["latency"])
            date, page, ep = q["date"], int(q["page"]), int(q.get("epoch", 0))
            with LOCK:
                STATE["calls"] += 1
                first = (date, page) not in STATE["seen"]
                STATE["seen"].add((date, page))
                STATE["log"].append((date, page))
            if page in CONFIG["fail_until_reload"] and ep == STATE.get("stuck_epoch", ep):
                STATE.setdefault("stuck_epoch", ep)
                return self._send(500, "{}")
            if page in CONFIG["fail_once"] and first:
                return self._send(500, "{}")
            total, rows = rows_for(date, page)
            if CONFIG["serve_other_date"]:
                _, rows = rows_for("2026-10-01" if False else date, page)
                rows = [[r[0], r[1].replace(date, "2026-10-01")] + r[2:] for r in rows]
            n_pages = max(1, -(-total // SIZE))
            if CONFIG["empty_glitch_last"] and page == n_pages and first and total:
                rows = []
            self._send(200, json.dumps({"total": total, "rows": rows}))
        else:
            self._send(404, "{}")


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):   # browser closing a socket is not an error here
        pass


def start(port=0):
    srv = QuietServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def reset(**over):
    CONFIG.update({"latency": 0.05, "skeleton": True, "fail_once": set(), "fail_until_reload": set(), "swallow": set(),
                   "numbers": False, "urlsync": False, "wrong_date_on_load": False, "empty_glitch_last": False,
                   "serve_other_date": False})
    CONFIG.update(over)
    STATE.update({"calls": 0, "seen": set(), "reload_epoch": 0, "log": []})
    STATE.pop("stuck_epoch", None)
