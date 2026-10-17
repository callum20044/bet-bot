"""Shadow variants: automatic variations of the bots that work, tested without touching the pot.

Each variant 'places' $5 shadow bets at real prices with real fees, tracked in shadow_trades and
settled on the real results - but it uses no cash and never blocks the main bots from a market.
The manager drops variants that clearly lose and promotes ones that prove themselves (300+ bets,
profitable, t >= 2) to real bets under the name 'v:<variant>'.

Families (from the three proven bots), split by price band, sport vs non-sport and time to close:
  fade  - bet NO; value = 1 - price x shrink      (longshot / underdog family)
  draw  - back football 'Tie'; value = price x 1.12 (draw family)
"""
import json
import math

from . import config
from .extra import _edge
from .models import Signal

SPORT_WORDS = ("GAME", "SPREAD", "TOTAL", "1H", "NFL", "NBA", "NHL", "MLB", "NCAA", "EPL", "UCL", "UEL", "LALIGA",
               "SERIEA", "BUNDESLIGA", "LIGUE", "MLS", "EFL", "ATP", "WTA", "PASS", "REC", "GOAL", "DOTA", "CS")


def is_sport(m):
    s = m.event_ticker.split("-")[0]
    return any(w in s for w in SPORT_WORDS)


def _grid():
    out = []
    for fam, shrink, bands in (("longshot", 0.75, [(0.02, 0.05), (0.05, 0.10), (0.10, 0.15)]),
                               ("underdog", 0.85, [(0.15, 0.20), (0.20, 0.25), (0.25, 0.30)])):
        for lo, hi in bands:
            for where in ("sport", "other"):
                for h0, h1 in ((0, 24), (24, 72)):
                    out.append(dict(name=f"{fam}-{int(lo*100)}to{int(hi*100)}c-{where}-{h0}to{h1}h", kind="fade",
                                    yes_min=lo, yes_max=hi, where=where, min_h=h0, max_h=h1, shrink=shrink, min_edge=0.005))
    for lo, hi in ((0.18, 0.25), (0.25, 0.32), (0.32, 0.40)):
        for h0, h1 in ((0, 24), (24, 48)):
            out.append(dict(name=f"draw-{int(lo*100)}to{int(hi*100)}c-{h0}to{h1}h", kind="draw",
                            yes_min=lo, yes_max=hi, where="any", min_h=h0, max_h=h1, uplift=1.12, min_edge=0.0))
    return out


VARIANTS = _grid()
MAX_OPEN = 40
SCHEMA = """CREATE TABLE IF NOT EXISTS shadow_trades (
  id INTEGER PRIMARY KEY, variant TEXT, opened_at TEXT, ticker TEXT, event_ticker TEXT, side TEXT,
  contracts INTEGER, price REAL, fee REAL, cost REAL, status TEXT DEFAULT 'open', payout REAL DEFAULT 0, settled_at TEXT)"""


def signal(v, m, now, name=None):
    if m.yes_mid is None:
        return None
    h = m.hours_to_close(now)
    if h is None or not (v["min_h"] < h <= v["max_h"]):
        return None
    mid = round(m.yes_mid, 4)
    if not (v["yes_min"] <= mid <= v["yes_max"]):
        return None
    if v["where"] == "sport" and not is_sport(m) or v["where"] == "other" and is_sport(m):
        return None
    if v["kind"] == "fade":
        if m.no_ask is None:
            return None
        est = 1 - mid * v["shrink"]
        e = _edge(est, m.no_ask)
        return Signal(m, "no", m.no_ask, est, e, name or v["name"], f"variant {v['name']}") if e >= v["min_edge"] else None
    if not m.ticker.endswith("-TIE") or m.yes_ask is None:
        return None
    est = min(mid * v["uplift"], 0.95)
    e = _edge(est, m.yes_ask)
    return Signal(m, "yes", m.yes_ask, est, e, name or v["name"], f"variant {v['name']}") if e >= v["min_edge"] else None


def _status(db):
    row = db.execute("SELECT v FROM meta WHERE k='variants'").fetchone()
    return json.loads(row[0]) if row else {}


def run(db, source, tradable, now, open_tickers=None):
    """Place shadow bets for variants still being tested; settle finished ones."""
    db.execute(SCHEMA)
    status = _status(db)
    testing = [v for v in VARIANTS if status.get(v["name"], {}).get("status", "testing") == "testing"]
    open_rows = db.execute("SELECT variant, ticker, event_ticker FROM shadow_trades WHERE status='open'").fetchall()
    held = {(r[0], r[1]) for r in open_rows}
    events = {(r[0], r[2]) for r in open_rows}
    counts = {}
    for r in open_rows:
        counts[r[0]] = counts.get(r[0], 0) + 1
    placed = 0
    stamp = now.isoformat()
    for m in tradable:
        for v in testing:
            if counts.get(v["name"], 0) >= MAX_OPEN or (v["name"], m.ticker) in held or (v["name"], m.event_ticker) in events:
                continue
            s = signal(v, m, now)
            if not s or not (config.MIN_PRICE <= s.price <= config.MAX_PRICE):
                continue
            n = max(1, math.floor(config.STAKE_PER_TRADE / s.price))
            fee = source.fee(n, s.price)
            db.execute("""INSERT INTO shadow_trades (variant, opened_at, ticker, event_ticker, side, contracts, price, fee, cost)
                          VALUES (?,?,?,?,?,?,?,?,?)""", (v["name"], stamp, m.ticker, m.event_ticker, s.side, n, s.price, fee,
                                                          round(n * s.price, 4)))
            held.add((v["name"], m.ticker))
            events.add((v["name"], m.event_ticker))
            counts[v["name"]] = counts.get(v["name"], 0) + 1
            placed += 1
    # settle: markets no longer open
    live = open_tickers if open_tickers is not None else {m.ticker for m in tradable}
    todo = db.execute("SELECT DISTINCT ticker FROM shadow_trades WHERE status='open'").fetchall()
    checked = settled = 0
    for (tk,) in todo:
        if tk in live or checked >= 150:
            continue
        checked += 1
        try:
            mk = source.get_market(tk)
        except Exception:
            continue
        if mk.result in ("yes", "no"):
            for row in db.execute("SELECT id, side, contracts FROM shadow_trades WHERE ticker=? AND status='open'", (tk,)).fetchall():
                won = mk.result == row[1]
                db.execute("UPDATE shadow_trades SET status=?, payout=?, settled_at=? WHERE id=?",
                           ("won" if won else "lost", float(row[2]) if won else 0.0, stamp, row[0]))
                settled += 1
        elif mk.status in ("finalized", "settled") or mk.result == "void":
            db.execute("UPDATE shadow_trades SET status='void', payout=cost+fee, settled_at=? WHERE ticker=? AND status='open'",
                       (stamp, tk))
    db.commit()
    print(f"  shadow: {len(testing)} variants testing, {placed} new shadow bets, {settled} settled")


def live_signals(db, tradable, now):
    """Real bets for variants the manager has promoted."""
    status = _status(db)
    live = [v for v in VARIANTS if status.get(v["name"], {}).get("status") == "live"]
    out = []
    for m in tradable:
        for v in live:
            s = signal(v, m, now, name="v:" + v["name"])
            if s:
                out.append(s)
    return out
