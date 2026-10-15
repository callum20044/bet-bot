"""Two pretend crypto bots on live Coinbase prices (added 15 Oct). Kept separate from the betting pot.

  eth_grid - $1,000 Ethereum grid: half in coin, half cash; buy $50 each time the price falls a full
             2% level below the last trade, sell $50 each time it rises a full level above it.
             Fixed grid (re-centring lost money in the check).
  dip_buy  - every Monday buy $50 each of Bitcoin and Ethereum; triple it when the coin is 20%+
             below its 90-day high.
State lives in the database (meta 'cryptobots'); every trade is logged in table crypto_trades.
"""
import json
import urllib.request
from datetime import datetime, timezone

FEE = 0.004
GRID = dict(coin="ETH-USD", capital=1000.0, spacing=0.02, levels=10)
DIP = dict(coins=["BTC-USD", "ETH-USD"], weekly=50.0, dip=0.80, boost=3)
UA = {"User-Agent": "Mozilla/5.0 (predbot paper trading)"}


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        return json.load(r)


def spot(product):
    return float(_get(f"https://api.exchange.coinbase.com/products/{product}/ticker")["price"])


def high_90d(product):
    rows = _get(f"https://api.exchange.coinbase.com/products/{product}/candles?granularity=86400")
    return max(r[2] for r in rows[:90])


def _log(db, now, bot, coin, side, price, qty, value, note=""):
    db.execute("""CREATE TABLE IF NOT EXISTS crypto_trades (ts TEXT, bot TEXT, coin TEXT, side TEXT,
                  price REAL, qty REAL, value REAL, note TEXT)""")
    db.execute("INSERT INTO crypto_trades VALUES (?,?,?,?,?,?,?,?)", (now.isoformat(), bot, coin, side, price, qty, value, note))


def run(db, now=None):
    now = now or datetime.now(timezone.utc)
    row = db.execute("SELECT v FROM meta WHERE k='cryptobots'").fetchone()
    st = json.loads(row[0]) if row else {}
    prices = {}
    for p in set([GRID["coin"]] + DIP["coins"]):
        try:
            prices[p] = spot(p)
        except Exception as e:
            print(f"  ! coinbase {p}: {e}")
    lines = []

    # ---------- ETH grid
    p = prices.get(GRID["coin"])
    if p:
        g = st.get("grid")
        if not g:
            cap = GRID["capital"]
            g = {"p0": p, "last": 0, "cash": cap / 2, "coin": cap / 2 / p * (1 - FEE), "trades": 0, "start": now.isoformat()}
            _log(db, now, "eth_grid", "ETH", "start", p, g["coin"], cap / 2, "half in coin, half cash")
        unit = GRID["capital"] / (2 * GRID["levels"])
        lvl = lambda k: g["p0"] * (1 + GRID["spacing"]) ** k
        while g["last"] > -GRID["levels"] and p <= lvl(g["last"] - 1):
            if g["cash"] >= unit:
                q = unit / p * (1 - FEE)
                g["coin"] += q
                g["cash"] -= unit
                g["trades"] += 1
                _log(db, now, "eth_grid", "ETH", "buy", p, q, unit, f"level {g['last'] - 1}")
            g["last"] -= 1
        while g["last"] < GRID["levels"] and p >= lvl(g["last"] + 1):
            q = unit / p
            if g["coin"] >= q:
                g["coin"] -= q
                g["cash"] += unit * (1 - FEE)
                g["trades"] += 1
                _log(db, now, "eth_grid", "ETH", "sell", p, q, unit, f"level {g['last'] + 1}")
            g["last"] += 1
        st["grid"] = g
        lines.append(f"ETH grid: ${g['cash'] + g['coin'] * p:,.2f} (ETH ${p:,.0f}, level {g['last']}, {g['trades']} trades)")

    # ---------- dip buyer (Mondays, once)
    d = st.get("dip") or {"coins": {}, "last_week": None}
    week = now.strftime("%G-W%V")
    if d["last_week"] is None or (now.weekday() == 0 and d["last_week"] != week):   # first buy straight away
        for c in DIP["coins"]:
            if c not in prices:
                continue
            try:
                hi = high_90d(c)
            except Exception as e:
                print(f"  ! coinbase candles {c}: {e}")
                continue
            amt = DIP["weekly"] * (DIP["boost"] if prices[c] < hi * DIP["dip"] else 1)
            h = d["coins"].setdefault(c, {"qty": 0.0, "put_in": 0.0})
            q = amt / prices[c] * (1 - FEE)
            h["qty"] += q
            h["put_in"] += amt
            _log(db, now, "dip_buy", c[:3], "buy", prices[c], q, amt,
                 f"90d high {hi:,.0f}" + (" - DIP, tripled" if amt > DIP["weekly"] else ""))
        d["last_week"] = week
    st["dip"] = d
    put = sum(h["put_in"] for h in d["coins"].values())
    val = sum(h["qty"] * prices.get(c, 0) for c, h in d["coins"].items())
    lines.append(f"dip buyer: put in ${put:,.0f}, worth ${val:,.2f}" if put else "dip buyer: first buy next Monday")

    db.execute("INSERT OR REPLACE INTO meta VALUES ('cryptobots', ?)", (json.dumps(st),))
    db.commit()
    print("  crypto: " + " | ".join(lines))
