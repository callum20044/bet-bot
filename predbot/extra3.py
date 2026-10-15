"""Outside-data bots (added 15 Oct, late): each brings in information Kalshi's crowd may be slow on.

  result_sniper - games ESPN says are FINISHED but Kalshi still trades below $1: buy the result.
  gas_price     - AAA daily petrol price markets: tomorrow ~ today + recent trend.
  football_elo  - ClubElo team ratings -> win / draw / loss chances vs Kalshi's football prices.
  tennis_elo    - Tennis Abstract player ratings -> match winner chances vs Kalshi.
  polymarket    - the same game priced differently on Polymarket and Kalshi.
  rotten        - Rotten Tomatoes score markets: once a film has lots of reviews its score barely moves.
"""
import difflib
import html
import json
import math
import re
import time
import unicodedata
import urllib.request
from datetime import datetime, timedelta, timezone
from statistics import NormalDist

from . import config
from .extra import _best
from .models import Signal

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept": "text/html,application/json;q=0.9,*/*;q=0.8", "Accept-Language": "en-GB,en;q=0.9"}
_cache = {}


def get(url, as_json=False):
    """Fetch once per run; failures are cached as None so a dead site costs one try."""
    if url in _cache:
        return _cache[url]
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as r:
            body = r.read().decode("utf-8", "replace")
        val = json.loads(body) if as_json else body
    except Exception as e:
        print(f"  ! fetch {url[:70]}: {e}")
        val = None
    _cache[url] = val
    return val


def norm(name):
    name = unicodedata.normalize("NFKD", html.unescape(name or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]", " ", name).split()


def same_team(kalshi_name, other):
    k, o = norm(kalshi_name), norm(other)
    return bool(k) and all(any(w.startswith(t) for w in o) for t in k)


def team_of(m):
    return m.title.split(" - ")[-1].strip()


def ticker_date(event_ticker):
    """'KXEPLGAME-26OCT17EVECHE' -> date(2026, 10, 17)."""
    mt = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", event_ticker)
    if not mt:
        return None
    try:
        return datetime.strptime(f"20{mt.group(1)}{mt.group(2)}{mt.group(3)}", "%Y%b%d").date()
    except ValueError:
        return None


def by_event(markets):
    ev = {}
    for m in markets:
        ev.setdefault(m.event_ticker, []).append(m)
    return ev


# ---------------------------------------------------------------- result sniper

ESPN = {
    "KXNFLGAME": "football/nfl", "KXNCAAFGAME": "football/college-football", "KXNBAGAME": "basketball/nba",
    "KXNHLGAME": "hockey/nhl", "KXMLBGAME": "baseball/mlb", "KXEPLGAME": "soccer/eng.1",
    "KXUCLGAME": "soccer/uefa.champions", "KXUELGAME": "soccer/uefa.europa", "KXUECLGAME": "soccer/uefa.europa.conf",
    "KXLALIGAGAME": "soccer/esp.1", "KXSERIEAGAME": "soccer/ita.1", "KXBUNDESLIGAGAME": "soccer/ger.1",
    "KXLIGUE1GAME": "soccer/fra.1", "KXMLSGAME": "soccer/usa.1", "KXEFLCHAMPIONSHIPGAME": "soccer/eng.2",
}


def espn_games(path, day):
    data = get(f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={day:%Y%m%d}", as_json=True)
    out = []
    for ev in (data or {}).get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        st = comp.get("status", {}).get("type", {})
        teams = []
        for c in comp.get("competitors", []):
            t = c.get("team", {})
            names = [t.get(k) for k in ("displayName", "shortDisplayName", "location", "name") if t.get(k)]
            try:
                score = float(c.get("score", "nan"))
            except ValueError:
                score = float("nan")
            teams.append((names, score))
        if len(teams) == 2:
            out.append({"done": bool(st.get("completed")) and st.get("state") == "post", "teams": teams})
    return out


def _match_game(kalshi_names, games):
    """The single ESPN game whose two teams match the two Kalshi team names."""
    hits = []
    for g in games:
        (n1, s1), (n2, s2) = g["teams"]
        a = [any(same_team(k, x) for x in n1) for k in kalshi_names]
        b = [any(same_team(k, x) for x in n2) for k in kalshi_names]
        if a[0] and b[1] and not a[1] and not b[0]:
            hits.append((g, {kalshi_names[0]: s1, kalshi_names[1]: s2}))
        elif a[1] and b[0] and not a[0] and not b[1]:
            hits.append((g, {kalshi_names[0]: s2, kalshi_names[1]: s1}))
    return hits[0] if len(hits) == 1 else None


def result_sniper(tradable, all_markets, now):
    c = config.SNIPER
    if not c["enabled"]:
        return []
    events, out = by_event(all_markets), []
    liquid = {m.ticker for m in tradable}
    for ev, ms in events.items():
        path = ESPN.get(ev.split("-")[0])
        day = ticker_date(ev)
        if not path or not day or not any(m.ticker in liquid for m in ms):
            continue
        teams = [m for m in ms if not m.ticker.endswith("-TIE")]
        tie = [m for m in ms if m.ticker.endswith("-TIE")]
        if len(teams) != 2:
            continue
        names = [team_of(m) for m in teams]
        games = []
        for d in (day - timedelta(days=1), day, day + timedelta(days=1)):
            games += espn_games(path, d)
        hit = _match_game(names, games)
        if not hit or not hit[0]["done"]:
            continue
        score = hit[1]
        if any(math.isnan(v) for v in score.values()):
            continue
        s0, s1 = score[names[0]], score[names[1]]
        result = {teams[0].ticker: s0 > s1, teams[1].ticker: s1 > s0}
        if tie:
            result[tie[0].ticker] = s0 == s1
        elif s0 == s1:
            continue          # a level score in a sport without a tie market: not finished as we think
        for m in ms:
            if m.ticker not in liquid or m.ticker not in result:
                continue
            side = "yes" if result[m.ticker] else "no"
            price = m.yes_ask if side == "yes" else m.no_ask
            if price is None or not (c["price_min"] <= price <= c["price_max"]):
                continue
            best = _best(m, c["certainty"] if side == "yes" else 1 - c["certainty"], c["min_edge"], c["price_min"], c["price_max"])
            if best and best[0] == side:
                out.append(Signal(m, side, best[2], best[1], best[3], "result_sniper",
                                  f"ESPN final: {names[0]} {s0:.0f} - {s1:.0f} {names[1]}; buying {side.upper()} at {best[2]*100:.0f}c"))
    return out


# ---------------------------------------------------------------- petrol prices

def aaa_prices(state=None):
    body = get("https://gasprices.aaa.com/" + (f"?state={state}" if state else ""))
    if not body:
        return None
    rows = {}
    for label in ("Current Avg.", "Yesterday Avg.", "Week Ago Avg."):
        i = body.find(label)
        mt = re.search(r"\$([\d.]+)", body[i:i + 200]) if i >= 0 else None
        if not mt:
            return None
        rows[label] = float(mt.group(1))
    return rows["Current Avg."], rows["Yesterday Avg."], rows["Week Ago Avg."]


def gas_price(tradable, now):
    c = config.GAS
    if not c["enabled"]:
        return []
    out, feeds = [], {}
    today_et = (now - timedelta(hours=4)).date()
    for m in tradable:
        series = m.event_ticker.split("-")[0]
        if not series.startswith("KXAAAGASD") or (m.raw or {}).get("strike_type") != "greater":
            continue
        day = ticker_date(m.event_ticker)
        if day != today_et + timedelta(days=1):
            continue          # only tomorrow's figure: one day of drift to forecast
        state = series[len("KXAAAGASD"):] or None
        if state not in feeds:
            feeds[state] = aaa_prices(state)
        f = feeds[state]
        if not f:
            continue
        cur, yest, week = f
        drift = 0.5 * (cur - yest) + 0.5 * (cur - week) / 7
        sd = c["base_sd"] + 0.5 * abs(cur - yest) + 0.3 * abs(cur - week) / 7
        mu = cur + drift
        k = m.raw["floor_strike"]
        p = 1 - NormalDist(mu, sd).cdf(k)
        best = _best(m, min(max(p, 0.01), 0.99), c["min_edge"], c["price_min"], c["price_max"])
        if best:
            side, est, price, e = best
            out.append(Signal(m, side, price, est, e, "gas_price",
                              f"AAA {state or 'US'} ${cur:.4f} (yday {yest:.4f}, wk {week:.4f}) -> tomorrow ~${mu:.4f}; "
                              f"above {k}: {p*100:.0f}% vs {m.yes_ask*100:.0f}c"))
    return out


# ---------------------------------------------------------------- football Elo

def clubelo(broker):
    """Today's ClubElo ratings, cached for a day in the database."""
    row = broker.db.execute("SELECT v FROM meta WHERE k='clubelo'").fetchone()
    if row:
        d = json.loads(row[0])
        if time.time() - d["t"] < 20 * 3600:
            return d["r"]
    body = get(f"http://api.clubelo.com/{datetime.now(timezone.utc):%Y-%m-%d}")
    if not body or "Club" not in body[:200]:
        return None
    ratings = {}
    for line in body.splitlines()[1:]:
        p = line.split(",")
        if len(p) > 4:
            try:
                ratings[p[1]] = float(p[4])
            except ValueError:
                pass
    broker.db.execute("INSERT OR REPLACE INTO meta VALUES ('clubelo', ?)", (json.dumps({"t": time.time(), "r": ratings}),))
    broker.db.commit()
    return ratings


def elo_lookup(name, ratings):
    """ClubElo squashes names ('ManCity', 'StTruiden'); match on squashed text."""
    key = "".join(norm(name))
    best, score = None, 0
    for club in ratings:
        r = difflib.SequenceMatcher(None, key, "".join(norm(club))).ratio()
        if r > score:
            best, score = club, r
    return ratings[best] if best and score >= 0.82 else None


def football_elo(tradable, all_markets, now, broker):
    c = config.FOOTBALL_ELO
    if not c["enabled"]:
        return []
    ratings = clubelo(broker)
    if not ratings:
        return []
    out = []
    for ev, ms in by_event(all_markets).items():
        if not ev.split("-")[0].endswith("GAME") or len(ms) != 3:
            continue
        tie = [m for m in ms if m.ticker.endswith("-TIE")]
        teams = [m for m in ms if not m.ticker.endswith("-TIE")]
        if len(tie) != 1 or len(teams) != 2:
            continue
        h = tie[0].hours_to_close(now)
        if h is None or not (c["min_hours_to_close"] <= h <= c["max_hours_to_close"]):
            continue
        # football tickers list the home team first (KXEPLGAME-26OCT17EVECHE = Everton at home to Chelsea)
        code = ev.split("-")[1][7:]
        e = [elo_lookup(team_of(m), ratings) for m in teams]
        if None in e:
            continue
        sfx = [m.ticker.rsplit("-", 1)[1] for m in teams]
        if code.startswith(sfx[0]) and code.endswith(sfx[1]):
            home = 0
        elif code.startswith(sfx[1]) and code.endswith(sfx[0]):
            home = 1
        else:
            continue
        dr = e[home] + c["home_adv"] - e[1 - home]
        exp_home = 1 / (1 + 10 ** (-dr / 400))
        d = abs(2 * exp_home - 1)
        p_draw = max(c["draw_base"] - c["draw_slope"] * d * d, 0.08)
        p_home = max(exp_home - p_draw / 2, 0.02)
        p_away = max(1 - exp_home - p_draw / 2, 0.02)
        probs = {teams[home].ticker: p_home, teams[1 - home].ticker: p_away, tie[0].ticker: p_draw}
        liquid = {m.ticker for m in tradable}
        for m in ms:
            if m.ticker not in liquid:
                continue
            p = probs[m.ticker]
            if abs(p - m.yes_mid) > c["max_disagree"]:
                continue          # far apart = probably a name mix-up, injury news or in-play; skip
            best = _best(m, p, c["min_edge"], 0.08, 0.92)
            if best:
                side, est, price, edg = best
                out.append(Signal(m, side, price, est, edg, "football_elo",
                                  f"ClubElo {team_of(teams[home])} {e[home]:.0f} (home) v {team_of(teams[1-home])} {e[1-home]:.0f}: "
                                  f"{team_of(m)} {p*100:.0f}% vs {m.yes_ask*100:.0f}c"))
    return out


# ---------------------------------------------------------------- tennis Elo

def tennis_ratings(tour):
    body = get(f"https://tennisabstract.com/reports/{tour}_elo_ratings.html")
    if not body:
        return {}
    out = {}
    for row in re.findall(r"<tr>(.*?)</tr>", body, re.S):
        name = re.search(r"player\.cgi\?p=[^\"]+\">([^<]+)</a>", row)
        nums = re.findall(r'<td align="right">([\d.]+)</td>', row)
        if name and len(nums) >= 5:
            elo, helo = float(nums[2]), float(nums[4])     # overall, hard-court
            out[html.unescape(name.group(1)).replace("\xa0", " ")] = (elo + helo) / 2
    return out


def player_elo(name, ratings):
    n = norm(name)
    if not n:
        return None
    hits = [v for k, v in ratings.items() if norm(k)[-1:] == n[-1:] and norm(k)[0][:1] == n[0][:1]]
    return hits[0] if len(hits) == 1 else None


def tennis_elo(tradable, all_markets, now):
    c = config.TENNIS_ELO
    if not c["enabled"]:
        return []
    tours = {"KXATPGAME": "atp", "KXWTAGAME": "wta"}
    out, liquid = [], {m.ticker for m in tradable}
    for ev, ms in by_event(all_markets).items():
        tour = tours.get(ev.split("-")[0])
        if not tour or len(ms) != 2 or not any(m.ticker in liquid for m in ms):
            continue
        h = ms[0].hours_to_close(now)
        if h is None or not (c["min_hours_to_close"] <= h <= c["max_hours_to_close"]):
            continue
        ratings = tennis_ratings(tour)
        e = [player_elo(team_of(m), ratings) for m in ms]
        if None in e:
            continue
        p0 = 1 / (1 + 10 ** ((e[1] - e[0]) / 400))
        for m, p in ((ms[0], p0), (ms[1], 1 - p0)):
            if m.ticker not in liquid or abs(p - m.yes_mid) > c["max_disagree"]:
                continue
            best = _best(m, p, c["min_edge"], 0.08, 0.92)
            if best:
                side, est, price, edg = best
                out.append(Signal(m, side, price, est, edg, "tennis_elo",
                                  f"Elo {team_of(ms[0])} {e[0]:.0f} v {team_of(ms[1])} {e[1]:.0f}: "
                                  f"{team_of(m)} {p*100:.0f}% vs {m.yes_ask*100:.0f}c"))
    return out


# ---------------------------------------------------------------- Polymarket

POLY_TAGS = {"KXNFLGAME": "nfl", "KXNBAGAME": "nba", "KXNHLGAME": "nhl", "KXMLBGAME": "mlb", "KXNCAAFGAME": "cfb",
             "KXEPLGAME": "epl", "KXUCLGAME": "ucl"}


def poly_prices(tag):
    """{(team name, date): probability} from Polymarket's open game markets for a sport."""
    data = get(f"https://gamma-api.polymarket.com/events?closed=false&limit=200&tag_slug={tag}", as_json=True) or []
    out = {}
    for ev in data:
        slug = ev.get("slug", "")
        mt = re.search(r"(\d{4}-\d{2}-\d{2})$", slug)
        if not mt:
            continue
        day = mt.group(1)
        for mk in ev.get("markets", []):
            try:
                outcomes = json.loads(mk.get("outcomes") or "[]")
                prices = [float(x) for x in json.loads(mk.get("outcomePrices") or "[]")]
            except (ValueError, TypeError):
                continue
            if len(outcomes) != 2 or len(prices) != 2 or mk.get("closed"):
                continue
            q = mk.get("question", "")
            if outcomes == ["Yes", "No"]:
                w = re.match(r"Will (.+?) win", q)
                if w:
                    out[(w.group(1), day)] = prices[0]
                elif re.search(r"\bdraw\b", q, re.I):
                    out[("Draw:" + q, day)] = prices[0]
            elif " vs" in q and "spread" not in q.lower() and "o/u" not in q.lower():
                for o, p in zip(outcomes, prices):
                    out[(o, day)] = p
    return out


def polymarket(tradable, now):
    c = config.POLY
    if not c["enabled"]:
        return []
    out, books = [], {}
    for m in tradable:
        series = m.event_ticker.split("-")[0]
        tag = POLY_TAGS.get(series)
        day = ticker_date(m.event_ticker)
        if not tag or not day or m.ticker.endswith("-TIE"):
            continue
        h = m.hours_to_close(now)
        if h is None or not (c["min_hours_to_close"] <= h <= c["max_hours_to_close"]):
            continue
        if tag not in books:
            books[tag] = poly_prices(tag)
        name = team_of(m)
        hits = [p for (t, d), p in books[tag].items()
                if not t.startswith("Draw:") and same_team(name, t)
                and abs((datetime.strptime(d, "%Y-%m-%d").date() - day).days) <= 1]
        if len(hits) != 1 or not (0.05 < hits[0] < 0.95):
            continue
        p = hits[0]
        if abs(p - m.yes_mid) > c["max_disagree"]:
            continue
        best = _best(m, p, c["min_edge"], 0.08, 0.92)
        if best:
            side, est, price, e = best
            out.append(Signal(m, side, price, est, e, "polymarket",
                              f"Polymarket {name} {p*100:.0f}% vs Kalshi {m.yes_ask*100:.0f}c"))
    return out


# ---------------------------------------------------------------- Rotten Tomatoes

def rt_score(title, tv=False):
    slug = re.sub(r"[^a-z0-9]+", "_", " ".join(norm(title))).strip("_")
    body = get(f"https://www.rottentomatoes.com/{'tv' if tv else 'm'}/{slug}")
    if not body:
        return None
    i = body.find('"criticsScore":')
    mt = re.search(r'"likedCount":(\d+),"notLikedCount":(\d+)', body[i:i + 400]) if i >= 0 else None
    if not mt:
        return None
    liked, disliked = int(mt.group(1)), int(mt.group(2))
    n = liked + disliked
    return (100 * liked / n, n) if n else None


def rotten(source, broker, now, ok):
    c = config.ROTTEN
    if not c["enabled"] or not hasattr(source, "list_series"):
        return []
    row = broker.db.execute("SELECT v FROM meta WHERE k='rt_series'").fetchone()
    cached = json.loads(row[0]) if row else {"t": 0, "s": []}
    if time.time() - cached["t"] > 24 * 3600:
        try:
            ss = source.list_series("Entertainment")
            cached = {"t": time.time(), "s": [s["ticker"] for s in ss if "rotten" in (s.get("title") or "").lower()]}
            broker.db.execute("INSERT OR REPLACE INTO meta VALUES ('rt_series', ?)", (json.dumps(cached),))
            broker.db.commit()
        except Exception as e:
            print(f"  ! rt series: {e}")
    out = []
    for st in cached["s"][: c["max_series"]]:
        try:
            ms = source.list_series_markets(st)
        except Exception:
            continue
        for m in ms:
            if not ok(m) or (m.raw or {}).get("strike_type") != "greater":
                continue
            film = re.sub(r"\s*Rotten Tomatoes score\??.*$", "", m.title.split(" - ")[0], flags=re.I).strip()
            sc = rt_score(film, tv=st.startswith("KXRTTV"))
            if not sc or sc[1] < c["min_reviews"]:
                continue
            score, n = sc
            sd = max(100 * math.sqrt(score / 100 * (1 - score / 100) / n), 0.5)
            k = m.raw["floor_strike"]
            p = 1 - NormalDist(score, sd).cdf(k + 0.5)
            best = _best(m, min(max(p, 0.02), 0.98), c["min_edge"], 0.05, 0.95)
            if best:
                side, est, price, e = best
                out.append(Signal(m, side, price, est, e, "rotten",
                                  f"{film}: RT {score:.0f}% from {n} reviews; above {k}: {p*100:.0f}% vs {m.yes_ask*100:.0f}c"))
    return out


def group_signals(tradable, all_markets, now, source, broker, ok):
    out = []
    for name, fn in (("sniper", lambda: result_sniper(tradable, all_markets, now)),
                     ("gas", lambda: gas_price(tradable, now)),
                     ("football_elo", lambda: football_elo(tradable, all_markets, now, broker)),
                     ("tennis_elo", lambda: tennis_elo(tradable, all_markets, now)),
                     ("polymarket", lambda: polymarket(tradable, now)),
                     ("rotten", lambda: rotten(source, broker, now, ok))):
        try:
            out += fn()
        except Exception as e:
            print(f"  ! {name} bot crashed: {e}")
    counts = {}
    for s in out:
        counts[s.strategy] = counts.get(s.strategy, 0) + 1
    print("  outside-data signals: " + (", ".join(f"{k} {v}" for k, v in counts.items()) or "none"))
    return out
