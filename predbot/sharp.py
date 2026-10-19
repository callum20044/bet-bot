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
import unicodedata
from statistics import NormalDist
import urllib.request
from datetime import datetime

from . import config
from .fees import fee_per_contract
from .models import Signal

API = "https://api.the-odds-api.com/v4/sports/{}/odds?regions=eu&markets=h2h&bookmakers=pinnacle&oddsFormat=decimal&apiKey={}"
# Kalshi series -> The Odds API sport, so a Seattle hockey game never matches the Seahawks
SPORT = {"KXEPLGAME": "soccer_epl", "KXUCLGAME": "soccer_uefa_champs_league",
         "KXNFLGAME": "americanfootball_nfl", "KXNBAGAME": "basketball_nba",
         "KXNFLSPREAD": "americanfootball_nfl", "KXNBASPREAD": "basketball_nba"}
STOP = {"fc", "cf", "sc", "afc", "club", "the", "de", "city", "united", "real", "st", "state"}


def words(name):
    return {w for w in re.findall(r"[a-z]+", (name or "").lower()) if len(w) > 2 and w not in STOP} or \
           set(re.findall(r"[a-z]+", (name or "").lower()))


def tokens(name):
    name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z0-9]+", name)


def same_team(kalshi_name, book_name):
    """Every word of Kalshi's (often shortened) name must start a word of the bookie's name:
    'Los Angeles C' fits 'Los Angeles Chargers' but not 'Los Angeles Rams'; 'Man City' fits
    'Manchester City'."""
    k, b = tokens(kalshi_name), tokens(book_name)
    return bool(k) and all(any(w.startswith(t) for w in b) for t in k)


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


PRE_START_MIN = 15   # minutes: Pinnacle odds are cached for hours, so only bet games that haven't started


def _not_started(start, now):
    return now is None or (start - now).total_seconds() / 60 >= PRE_START_MIN


def match(m, events, team=None, now=None):
    """(Pinnacle probability, event, outcome name) for this Kalshi team market, or None.
    Same sport, kick-off a few hours before Kalshi expects the result, and exactly one bookie
    team that fits the name - anything ambiguous is skipped."""
    sport = SPORT.get(m.event_ticker.split("-")[0])
    sub = team or (m.title.split(" - ")[-1] if " - " in m.title else m.title)
    when = m.resolve_time
    if sport is None or when is None:
        return None
    hits = []
    for ev in events:
        if ev.get("sport_key") != sport:
            continue
        try:
            start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if not (0 <= (when - start).total_seconds() / 3600 <= 8):
            continue          # Kalshi expects the result a few hours after the start
        if not _not_started(start, now):
            continue          # in play: the cached pre-match odds no longer mean anything (fix 19 Oct)
        for name, p in fair_probs(ev).items():
            if name != "Draw" and same_team(sub, name):
                hits.append((p, ev, name))
    return hits[0] if len(hits) == 1 else None


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
        hit = match(m, events, now=now)
        if not hit:
            continue
        p, ev, name = hit
        best = None
        for side, pr, price in (("yes", p, m.yes_ask), ("no", 1 - p, m.no_ask)):
            if price is None or not (c["price_min"] <= price <= c["price_max"]):
                continue
            edge = pr - price - fee_per_contract(price)
            if abs(pr - price) > c.get("max_disagree", 1):
                continue      # a huge gap usually means a mismatch or stale odds, not value
            if edge >= c["min_edge"] and (best is None or edge > best[3]):
                best = (side, pr, price, edge)
        if best:
            side, pr, price, edge = best
            out.append(Signal(m, side, price, pr, edge, "sharp",
                              f"Pinnacle {name} {p*100:.0f}% vs Kalshi {m.yes_ask*100:.0f}c"))
    return out


def candidate_events(m, events, now=None):
    """Pinnacle games in the same sport that kick off a few hours before Kalshi expects the result."""
    sport = SPORT.get(m.event_ticker.split("-")[0])
    when = m.resolve_time
    if sport is None or when is None:
        return []
    out = []
    for ev in events:
        if ev.get("sport_key") != sport:
            continue
        try:
            start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if 0 <= (when - start).total_seconds() / 3600 <= 8 and _not_started(start, now):
            out.append(ev)
    return out


def best_side(p, m, lo, hi, min_edge, max_disagree=1.0):
    best = None
    for side, pr, price in (("yes", p, m.yes_ask), ("no", 1 - p, m.no_ask)):
        if price is None or not (lo <= price <= hi) or abs(pr - price) > max_disagree:
            continue
        edge = pr - price - fee_per_contract(price)
        if edge >= min_edge and (best is None or edge > best[3]):
            best = (side, pr, price, edge)
    return best


def draw_signals(markets, all_markets, now, events, ok=lambda m: True):
    """Kalshi football 'Tie' markets vs Pinnacle's draw price."""
    c = config.SHARP_DRAW
    if not c["enabled"]:
        return []
    teams = {}
    for m in all_markets:
        if not m.ticker.endswith("-TIE"):
            teams.setdefault(m.event_ticker, []).append(m.title.split(" - ")[-1])
    out = []
    for m in markets:
        if not m.ticker.endswith("-TIE") or not ok(m) or SPORT.get(m.event_ticker.split("-")[0], "")[:6] != "soccer":
            continue
        names = teams.get(m.event_ticker, [])
        if len(names) != 2:
            continue
        hits = []
        for ev in candidate_events(m, events, now):
            probs = fair_probs(ev)
            sides = [n for n in probs if n != "Draw"]
            if "Draw" in probs and len(sides) == 2 and (
                    (same_team(names[0], sides[0]) and same_team(names[1], sides[1])) or
                    (same_team(names[0], sides[1]) and same_team(names[1], sides[0]))):
                hits.append((probs["Draw"], ev))
        if len(hits) != 1:
            continue
        p, ev = hits[0]
        best = best_side(p, m, c["price_min"], c["price_max"], c["min_edge"], c.get("max_disagree", 1.0))
        if best:
            side, pr, price, edge = best
            out.append(Signal(m, side, price, pr, edge, "sharp_draw",
                              f"Pinnacle draw {p*100:.0f}% ({ev['home_team']} v {ev['away_team']}) vs Kalshi {m.yes_ask*100:.0f}c"))
    return out


SPREAD_RE = re.compile(r"^(.*?) wins by over ([\d.]+) points", re.I)


def spread_signals(markets, now, events, ok=lambda m: True):
    """Kalshi 'X wins by over N points' markets, priced from Pinnacle's win odds: a team that wins
    p of the time has an expected margin of sd * z(p), and real margins scatter around that
    with a spread of about sd points."""
    c = config.SHARP_SPREAD
    if not c["enabled"]:
        return []
    out = []
    for m in markets:
        series = m.event_ticker.split("-")[0]
        if series not in c["sd"] or not ok(m):
            continue
        hit = SPREAD_RE.match(m.title.split(" - ")[-1])
        if not hit:
            continue
        team, line = hit.group(1), float(hit.group(2))
        found = match(m, events, team=team, now=now)
        if not found:
            continue
        p_win, ev, name = found
        if not (0.03 < p_win < 0.97):
            continue
        sd = c["sd"][series]
        mu = sd * NormalDist().inv_cdf(p_win)
        p = 1 - NormalDist(mu, sd).cdf(line)
        best = best_side(p, m, c["price_min"], c["price_max"], c["min_edge"], c.get("max_disagree", 1.0))
        if best:
            side, pr, price, edge = best
            out.append(Signal(m, side, price, pr, edge, "sharp_spread",
                              f"Pinnacle {name} wins {p_win*100:.0f}% -> by {line}+ {p*100:.0f}% vs Kalshi {m.yes_ask*100:.0f}c"))
    return out


def extra_signals(markets, all_markets, now, broker):
    """Draw and spread bots: share the Pinnacle odds already downloaded for the sharp bot."""
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if not key or not (config.SHARP_DRAW["enabled"] or config.SHARP_SPREAD["enabled"]):
        return []
    events = load_odds(broker, config.SHARP, key)
    if not events:
        return []
    d, sp = draw_signals(markets, all_markets, now, events), spread_signals(markets, now, events)
    ties = [m for m in markets if m.ticker.endswith("-TIE") and SPORT.get(m.event_ticker.split("-")[0], "").startswith("soccer")]
    spreads = [m for m in markets if m.event_ticker.split("-")[0] in config.SHARP_SPREAD["sd"]]
    matched = sum(1 for m in spreads if (h := SPREAD_RE.match(m.title.split(" - ")[-1])) and match(m, events, team=h.group(1), now=now))
    print(f"  pinnacle extras: {len(events)} games; ties {len(ties)} -> {len(d)} bets; "
          f"spreads {len(spreads)} ({matched} matched) -> {len(sp)} bets")
    return d + sp
