"""Tiny local dashboard. Run:  python -m predbot.dashboard   then open http://localhost:8050"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config
from .broker import connect

PORT = 8050


def state(db):
    start = float((db.execute("SELECT v FROM meta WHERE k='start'").fetchone() or [config.STARTING_BANKROLL])[0])
    src = (db.execute("SELECT v FROM meta WHERE k='source'").fetchone() or ["?"])[0]
    trades = [dict(r) for r in db.execute("SELECT * FROM trades ORDER BY id DESC")]
    spent = sum(t["cost"] + t["fee"] for t in trades)
    paid = sum(t["payout"] for t in trades)
    cash = start - spent + paid
    open_ = [t for t in trades if t["status"] == "open"]
    closed = [t for t in trades if t["status"] in ("won", "lost")]
    open_val = sum((t["mark"] if t["mark"] is not None else t["price"]) * t["contracts"] for t in open_)
    realized = sum(t["payout"] - t["cost"] - t["fee"] for t in trades if t["status"] != "open")
    unrealized = sum(((t["mark"] if t["mark"] is not None else t["price"]) * t["contracts"]) - t["cost"] - t["fee"]
                     for t in open_)
    wins = sum(1 for t in closed if t["status"] == "won")

    strat = {}
    for t in trades:
        s = strat.setdefault(t["strategy"], dict(name=t["strategy"], bets=0, open=0, won=0, lost=0, pnl=0.0,
                                                  exp=0.0, staked=0.0))
        s["bets"] += 1
        if t["status"] == "open":
            s["open"] += 1
        else:
            s["pnl"] += t["payout"] - t["cost"] - t["fee"]
            s["exp"] += t["edge"] * t["contracts"]
            s["staked"] += t["cost"] + t["fee"]
            if t["status"] == "won":
                s["won"] += 1
            elif t["status"] == "lost":
                s["lost"] += 1

    last = db.execute("SELECT MAX(ts) FROM equity").fetchone()[0]
    eq = [dict(r) for r in db.execute("SELECT ts, equity FROM equity ORDER BY ts")]
    if len(eq) > 600:     # thin it out for drawing
        k = len(eq) // 600 + 1
        eq = eq[::k] + [eq[-1]]
    return dict(
        source=src, start=start, cash=cash, equity=cash + open_val, realized=realized, unrealized=unrealized,
        win_rate=(wins / len(closed)) if closed else None, settled=len(closed), wins=wins,
        total_fees=sum(t["fee"] for t in trades),
        strategies=list(strat.values()), open=open_[:100],
        updated=last,
        recent=[t for t in trades if t["status"] != "open"][:50], curve=eq,
    )


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Paper Bot</title><style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--mute:#6b7280;--line:#e5e7eb;--up:#0f8a4b;--down:#c2352b;--accent:#4f46e5}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#181b22;--ink:#e8eaef;--mute:#9097a3;--line:#2a2f3a;--up:#3fcf8e;--down:#ff6b5e;--accent:#8b85ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:20px 16px 60px}h1{font-size:20px;margin:0 0 4px}.sub{color:var(--mute);margin-bottom:18px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.k{color:var(--mute);font-size:12px;text-transform:uppercase;letter-spacing:.04em}.v{font-size:22px;font-weight:600;margin-top:4px;font-variant-numeric:tabular-nums}
.up{color:var(--up)}.down{color:var(--down)}h2{font-size:15px;margin:22px 0 8px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--mute);font-weight:500;font-size:12px}td.t{white-space:normal;min-width:220px}.wrap{overflow-x:auto}
svg{width:100%;height:200px;display:block}.note{color:var(--mute);font-size:12px;margin-top:6px}
.pill{display:inline-block;padding:1px 7px;border-radius:99px;font-size:12px;border:1px solid var(--line)}
</style></head><body><main>
<h1>Paper trading bot</h1><div class="sub" id="sub">loading…</div>
<div class="grid" id="kpis"></div>
<div class="card"><div class="k">Equity over time</div><svg id="chart" viewBox="0 0 1000 200" preserveAspectRatio="none"></svg>
<div class="note">Equity = cash + open bets valued at what you could sell them for right now.</div></div>
<h2>Strategy scoreboard</h2><div class="card wrap"><table id="strat"></table>
<div class="note">"Expected" is what the strategy predicted it would make on settled bets. If actual keeps falling short of expected, its assumption is wrong.</div></div>
<h2>Open bets</h2><div class="card wrap"><table id="open"></table></div>
<h2>Recently settled</h2><div class="card wrap"><table id="recent"></table></div>
</main><script>
const $=s=>document.querySelector(s),usd=v=>(v<0?"-$":"$")+Math.abs(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
const sgn=v=>`<span class="${v>=0?'up':'down'}">${v>=0?'+':''}${usd(v).replace('-','')}</span>`.replace('$',v<0?'-$':'$').replace('+-','-');
const pnl=v=>`<span class="${v>=0?'up':'down'}">${v>=0?'+':'-'}$${Math.abs(v).toFixed(2)}</span>`;
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function load(){
 let d;try{d=await (await fetch((window.API_URL||'/api/state')+'?t='+Date.now())).json()}catch(e){$('#sub').textContent='Dashboard can\'t reach the database - is it running?';return}
 const tot=d.equity-d.start;
 $('#sub').textContent=`Source: ${d.source} · started with ${usd(d.start)} paper money`+(d.updated?` · last scan ${new Date(d.updated).toLocaleString()}`:'');
 $('#kpis').innerHTML=[
  ['Equity',usd(d.equity)],['Total P&L',pnl(tot)+` <span class="k">${(tot/d.start*100).toFixed(1)}%</span>`],
  ['Realized (settled)',pnl(d.realized)],['Unrealized (open)',pnl(d.unrealized)],
  ['Win rate',d.win_rate==null?'–':(d.win_rate*100).toFixed(1)+'%'+` <span class="k">${d.wins}/${d.settled}</span>`],
  ['Fees paid',usd(d.total_fees)],['Open bets',d.open.length]
 ].map(([k,v])=>`<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
 const e=d.curve,svg=$('#chart');
 if(e.length>1){const ys=e.map(p=>p.equity).concat([d.start]),lo=Math.min(...ys),hi=Math.max(...ys),r=(hi-lo)||1;
  const pt=(p,i)=>`${(i/(e.length-1)*1000).toFixed(1)},${(190-(p.equity-lo)/r*180).toFixed(1)}`;
  const by=(190-(d.start-lo)/r*180).toFixed(1);
  svg.innerHTML=`<line x1="0" x2="1000" y1="${by}" y2="${by}" stroke="var(--line)" stroke-dasharray="4 4"/>
  <polyline fill="none" stroke="var(--accent)" stroke-width="2" vector-effect="non-scaling-stroke" points="${e.map(pt).join(' ')}"/>`}
 $('#strat').innerHTML='<tr><th>Strategy</th><th>Bets</th><th>Open</th><th>Won</th><th>Lost</th><th>Win rate</th><th>Actual P&L</th><th>Expected</th><th>Return on stake</th></tr>'+
  d.strategies.map(s=>{const n=s.won+s.lost;return `<tr><td>${esc(s.name)}</td><td>${s.bets}</td><td>${s.open}</td><td>${s.won}</td><td>${s.lost}</td>
  <td>${n?(s.won/n*100).toFixed(1)+'%':'–'}</td><td>${pnl(s.pnl)}</td><td>${n?pnl(s.exp):'–'}</td><td>${s.staked?(s.pnl/s.staked*100).toFixed(1)+'%':'–'}</td></tr>`}).join('');
 $('#open').innerHTML='<tr><th>Market</th><th>Side</th><th>Qty</th><th>Paid</th><th>Now</th><th>P&L</th><th>Strategy</th><th>Closes</th></tr>'+
  (d.open.map(t=>{const m=t.mark??t.price,u=m*t.contracts-t.cost-t.fee;return `<tr><td class="t">${esc(t.title)}</td><td><span class="pill">${t.side.toUpperCase()}</span></td>
  <td>${t.contracts}</td><td>${(t.price*100).toFixed(0)}¢</td><td>${(m*100).toFixed(0)}¢</td><td>${pnl(u)}</td><td>${esc(t.strategy)}</td><td>${(t.close_time||'').slice(0,16).replace('T',' ')}</td></tr>`}).join('')||'<tr><td colspan=8 class="k">none</td></tr>');
 $('#recent').innerHTML='<tr><th>Market</th><th>Side</th><th>Result</th><th>P&L</th><th>Strategy</th><th>Why</th></tr>'+
  (d.recent.map(t=>`<tr><td class="t">${esc(t.title)}</td><td><span class="pill">${t.side.toUpperCase()}</span></td><td>${t.status}</td>
  <td>${pnl(t.payout-t.cost-t.fee)}</td><td>${esc(t.strategy)}</td><td class="t">${esc(t.reason)}</td></tr>`).join('')||'<tr><td colspan=6 class="k">none yet</td></tr>');
}
load();setInterval(load,10000);
</script></body></html>"""


def serve(db_path=config.DB_PATH, port=PORT):
    db = connect(db_path)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/api/state"):
                body, ctype = json.dumps(state(db)).encode(), "application/json"
            elif self.path in ("/", "/index.html"):
                body, ctype = PAGE.encode(), "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    print(f"Dashboard: http://localhost:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=config.DB_PATH)
    ap.add_argument("--port", type=int, default=PORT)
    a = ap.parse_args()
    serve(a.db, a.port)
