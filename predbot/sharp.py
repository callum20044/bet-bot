"""Sharp-bookie comparison: Kalshi game prices vs Pinnacle's odds.

Pinnacle is the bookmaker professional bettors use; its prices are about the most accurate
public estimate of a game's chances. We fetch them from The Odds API (free key, 500 requests a
month), strip out the bookie's margin, match each game to Kalshi's "who wins" markets, and bet
when Kalshi is off by enough.

Sleeps until a key is added: GitHub repo -> Settings -> Secrets and variables -> Actions ->
New repository secret, name ODDS_API_KEY.
"""
import json
import os
import re
import time
import urllib.request
from datetime import datetime

from . import config
from .fees import fee_per_contract
from .models import Signal

API = "https://api.the-odds-api.com/v4/sports/{}/odds?regions=eu&markets=h2h&bookmakers=pinnacle&oddsFormat=decimal&apiKey={}"
STOP = {"fc", "cf", "sc", "afc", "club", "the", "de", "city", "united", "real", "st", "state"}


def words(name):
    return {w for w in re.findall(r"[a-z]+", (name or "").lower()) if len(w) > 2 and w not in STOP} or \
           set(re.findall(r"[a-z]+", (name or "").lower()))


def fair_probs(event):
    """{outcome name: probability} with Pinnacle's margin removed."""
    for b in event.get("bookmakers", []):
        for mk in b.get("markets", []):
            if mk.get("key") == "h2h":
                inv = {o["name"]: 1 / o["price"] for o in mk["outcomes"] if o.get("price")}
                tot = sum(inv.values())
                return {k: v / tot for k, v in inv.items()} if tot else {}
    return {}


def load_odds(broker, c, key):
    """Cached in the database; refreshed every few hours to stay inside the free allowance."""
    db = broker.db
    row = db.execute("SELECT v FROM meta WHERE k='odds_cache'").fetchone()
    cache = json.loads(row[0]) if row else {"t": 0, "events": []}
    if time.time() - cache["t"] < c["refresh_hours"] * 3600:
        return cache["events"]
    events = []
    for sport in c["sports"]:
        try:
            req = urllib.request.Request(API.format(sport, key), headers={"User-Agent": "predbot"})
            with urllib.request.urlopen(req, timeout=30) as r:
                events += json.load(r)
        except Exception as e:
            print(f"  ! odds api {sport}: {e}")
    cache = {"t": time.time(), "events": events}
    db.execute("INSERT OR REPLACE INTO meta VALUES ('odds_cache', ?)", (json.dumps(cache),))
    db.commit()
    return events


def match(m, events):
    """(Pinnacle probability, event, outcome name) for this Kalshi team market, or None."""
    sub = m.title.split(" - ")[-1] if " - " in m.title else m.title
    when = m.resolve_time
    if when is None:
        return None
    for ev in events:
        try:
            start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if not (0 <= (when - start).total_seconds() / 3600 <= 8):
            continue          # Kalshi expects the result a few hours after the start
        for name, p in fair_probs(ev).items():
            if name != "Draw" and words(name) & words(sub):
                return p, ev, name
    return None


def signals(markets, now, broker, ok=lambda m: True):
    c = config.SHARP
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if not c["enabled"] or not key:
        return []
    events = load_odds(broker, c, key)
    if not events:
        return []
    out = []
    for m in markets:
        if "GAME" not in m.event_ticker.split("-")[0] or m.ticker.endswith("-TIE") or not ok(m):
            continue
        hit = match(m, events)
        if not hit:
            continue
        p, ev, name = hit
        best = None
        for side, pr, price in (("yes", p, m.yes_ask), ("no", 1 - p, m.no_ask)):
            if price is None or not (c["price_min"] <= price <= c["price_max"]):
                continue
            edge = pr - price - fee_per_contract(price)
            if edge >= c["min_edge"] and (best is None or edge > best[3]):
                best = (side, pr, price, edge)
        if best:
            side, pr, price, edge = best
            out.append(Signal(m, side, price, pr, edge, "sharp",
                              f"Pinnacle {name} {p*100:.0f}% vs Kalshi {m.yes_ask*100:.0f}c"))
    return out
