"""Five more strategies (added 15 Oct, evening).

  half_draw       - football half-time 'Tie' markets vs a half-time draw model (draws are far more
                    common at half-time: ~42% of first halves end level).
  goals_poisson   - football 'over X goals' ladders: fit one Poisson goals curve to the whole ladder
                    and bet the rung that sits furthest off it.
  lowscore_spread - NHL / MLB 'wins by over 1.5' priced from Kalshi's own 'wins' price with a
                    goals/runs (Poisson) model, like spread_moneyline does for NFL/NBA.
  crypto_iv       - Bitcoin / Ethereum price markets priced with the options market's own volatility
                    forecast (Deribit DVOL) instead of the last two hours' wiggles, which lost before.
  mention_fade    - NO on 'will X say Y' markets: a retail favourite where YES looks overpriced.

All the sports ones only bet before kick-off; in-play prices know the score and the models don't.
"""
import json
import math
import re
import time
import urllib.request

from . import config, crypto
from .extra import _best, _edge, _hours_ok, _logit
from .models import Signal

US = ("NFL", "NBA", "NHL", "MLB", "NCAA", "WNBA")


def _pre_match(m, now, min_h, max_h):
    h = m.hours_to_close(now)
    return h is not None and min_h <= h <= max_h


def _pois(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


# ---------- half-time draws ----------

def half_draw(tradable, all_markets, now):
    c = config.HALF_DRAW
    if not c["enabled"]:
        return []
    by_ticker = {m.ticker: m for m in all_markets}
    events = {}
    for m in all_markets:
        events.setdefault(m.event_ticker, []).append(m)
    out = []
    for m in tradable:
        series = m.event_ticker.split("-")[0]
        if not m.ticker.endswith("-TIE") or not series.endswith("1H") or any(x in series for x in US):
            continue
        game_ev = series[:-2] + "GAME-" + m.event_ticker.split("-", 1)[1]
        teams = [x for x in events.get(game_ev, []) if not x.ticker.endswith("-TIE")]
        tie_ft = by_ticker.get(game_ev + "-TIE")
        if len(teams) != 2 or not tie_ft or any(x.yes_mid is None for x in teams + [tie_ft]):
            continue
        if not _pre_match(tie_ft, now, c["min_hours_full_time"], c["max_hours_to_close"]):
            continue          # use the full-time market's clock: is the game still to start?
        s = teams[0].yes_mid + teams[1].yes_mid + tie_ft.yes_mid
        if not (0.90 <= s <= 1.15):
            continue
        d = abs(teams[0].yes_mid - teams[1].yes_mid) / s
        p = c["base"] - c["slope"] * d * d
        best = _best(m, p, c["min_edge"], 0.10, 0.90)
        if best:
            side, est, price, e = best
            out.append(Signal(m, side, price, est, e, "half_draw",
                              f"teams {teams[0].yes_mid*100:.0f}c v {teams[1].yes_mid*100:.0f}c -> HT draw ~{p*100:.0f}% vs {m.yes_ask*100:.0f}c"))
    return out


# ---------- football goals ladders ----------

GOALS_RE = re.compile(r"over ([\d.]+) goals", re.I)


def goals_poisson(tradable, now, all_markets=()):
    c = config.GOALS
    if not c["enabled"]:
        return []
    # a goal already scored shows up as 'over 0.5' at ~100%: that game has started, skip it
    started = {m.event_ticker for m in all_markets
               if (m.raw or {}).get("floor_strike") == 0.5 and (m.yes_bid or 0) >= 0.97}
    ladders = {}
    for m in tradable:
        series = m.event_ticker.split("-")[0]
        r = m.raw or {}
        if "TOTAL" not in series or "TEAM" in series or any(x in series for x in US) or "1H" in series:
            continue
        if r.get("strike_type") != "greater" or r.get("floor_strike") is None or not GOALS_RE.search(m.title):
            continue
        if m.event_ticker in started or not _pre_match(m, now, c["min_hours_to_close"], c["max_hours_to_close"]):
            continue
        ladders.setdefault(m.event_ticker, []).append(m)
    out = []
    for ms in ladders.values():
        rungs = [m for m in ms if 0.05 <= m.yes_mid <= 0.95]
        if len(rungs) < c["min_rungs"]:
            continue

        def p_over(line, lam):
            return 1 - sum(_pois(k, lam) for k in range(int(math.floor(line)) + 1))

        best_lam, best_err = None, None
        for i in range(50, 600):
            lam = i / 100
            err = sum((_logit(m.yes_mid) - _logit(p_over(m.raw["floor_strike"], lam))) ** 2 for m in rungs)
            if best_err is None or err < best_err:
                best_lam, best_err = lam, err
        worst = max(rungs, key=lambda m: abs(m.yes_mid - p_over(m.raw["floor_strike"], best_lam)))
        fit = p_over(worst.raw["floor_strike"], best_lam)
        d = worst.yes_mid - fit
        if abs(d) < c["min_gap"]:
            continue
        p = fit + c["trust_rung"] * d
        best = _best(worst, p, c["min_edge"], 0.08, 0.92)
        if best:
            side, est, price, e = best
            out.append(Signal(worst, side, price, est, e, "goals_poisson",
                              f"ladder fits {best_lam:.2f} goals; over {worst.raw['floor_strike']} at "
                              f"{worst.yes_mid*100:.0f}c vs fit {fit*100:.0f}%"))
    return out


# ---------- NHL / MLB spreads ----------

SPREAD_RE = re.compile(r"^(.*?) wins by over ([\d.]+) (goals|runs)", re.I)


def _margin_probs(lam_a, lam_b, n=25):
    pa = [_pois(k, lam_a) for k in range(n)]
    pb = [_pois(k, lam_b) for k in range(n)]
    diff = {}
    for i in range(n):
        for j in range(n):
            diff[i - j] = diff.get(i - j, 0) + pa[i] * pb[j]
    return diff


def lowscore_spread(tradable, all_markets, now):
    c = config.LOWSCORE
    if not c["enabled"]:
        return []
    by_ticker = {m.ticker: m for m in all_markets}
    out = []
    for m in tradable:
        series = m.event_ticker.split("-")[0]
        total = c["total"].get(series)
        if total is None or not _pre_match(m, now, c["min_hours_to_close"], c["max_hours_to_close"]):
            continue
        hit = SPREAD_RE.match(m.title.split(" - ")[-1])
        if not hit:
            continue
        line = float(hit.group(2))
        code = re.sub(r"\d+$", "", m.ticker.rsplit("-", 1)[1])
        game_ev = m.event_ticker.replace("SPREAD", "GAME", 1)
        mine = by_ticker.get(f"{game_ev}-{code}")
        if not mine or mine.yes_mid is None or mine.spread is None or mine.spread > 0.05:
            continue
        others = [x for t, x in by_ticker.items() if t.startswith(game_ev + "-") and t != mine.ticker and x.yes_mid is not None]
        p_win = mine.yes_mid / (mine.yes_mid + others[0].yes_mid) if len(others) == 1 else mine.yes_mid
        if not (0.10 < p_win < 0.90):
            continue
        # split the expected total between the teams so that wins (+ half of regulation ties, which
        # go to overtime / extra innings) match Kalshi's price
        lo, hi = 0.05, 0.95
        for _ in range(40):
            s = (lo + hi) / 2
            diff = _margin_probs(total * s, total * (1 - s))
            pw = sum(v for k, v in diff.items() if k > 0) + 0.5 * diff.get(0, 0)
            lo, hi = (s, hi) if pw < p_win else (lo, s)
        p = sum(v for k, v in diff.items() if k > line)
        best = _best(m, p, c["min_edge"], 0.08, 0.92)
        if best:
            side, est, price, e = best
            out.append(Signal(m, side, price, est, e, "lowscore_spread",
                              f"Kalshi has {code} winning {p_win*100:.0f}% -> by {line}+ ~{p*100:.0f}% vs {m.yes_ask*100:.0f}c"))
    return out


# ---------- crypto with implied volatility ----------

DVOL = "https://www.deribit.com/api/v2/public/get_volatility_index_data?currency={}&start_timestamp={}&end_timestamp={}&resolution=3600"


def dvol(currency):
    end = int(time.time() * 1000)
    req = urllib.request.Request(DVOL.format(currency, end - 6 * 3600 * 1000, end), headers={"User-Agent": "predbot"})
    with urllib.request.urlopen(req, timeout=20) as r:
        rows = json.load(r)["result"]["data"]
    return float(rows[-1][4]) / 100          # annualised, e.g. 0.45


def crypto_iv(tradable, now):
    c = config.CRYPTO_IV
    if not c["enabled"]:
        return []
    feeds, out = {}, []
    for m in tradable:
        product = crypto.SERIES.get(m.event_ticker.split("-")[0])
        if not product or not m.raw:
            continue
        mins = m.hours_to_close(now)
        if mins is None:
            continue
        mins *= 60
        if not (c["min_minutes"] <= mins <= c["max_minutes"]):
            continue
        if product not in feeds:
            try:
                spot = crypto.spot_and_vol(crypto.candles(product), 30)[0]
                iv = dvol(product[:3])
                feeds[product] = (spot, iv / math.sqrt(365 * 24 * 60))
            except Exception as e:
                print(f"  ! crypto_iv {product}: {e}")
                feeds[product] = None
        if not feeds[product]:
            continue
        spot, sd_min = feeds[product]
        try:
            p = crypto.prob(m.raw, spot, sd_min, mins)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        if p is None:
            continue
        p = min(max(p, 0.01), 0.99)
        best = _best(m, p, c["min_edge"], c["price_min"], c["price_max"])
        if best:
            side, est, price, e = best
            out.append(Signal(m, side, price, est, e, "crypto_iv",
                              f"{product[:3]} ${spot:,.0f}, options vol {sd_min*math.sqrt(525600)*100:.0f}%, "
                              f"{mins:.0f}m left -> YES {p*100:.0f}% vs {m.yes_ask*100:.0f}c"))
    return out


# ---------- 'will X say Y' ----------

MENTION_RE = re.compile(r"\b(say|says|said|mention|mentions)\b", re.I)


def mention_fade(m, now, broker):
    c = config.MENTION
    if not c["enabled"] or m.yes_mid is None or not _hours_ok(m, now, c["max_hours_to_close"]):
        return None
    if "MENTION" not in m.event_ticker.upper() and not MENTION_RE.search(m.title):
        return None
    mid = round(m.yes_mid, 4)
    if not (c["yes_min"] <= mid <= c["yes_max"]) or m.no_ask is None:
        return None
    est_no = 1 - mid * c["shrink"]
    e = _edge(est_no, m.no_ask)
    if e < c["min_edge"]:
        return None
    return Signal(m, "no", m.no_ask, est_no, e, "mention_fade", f"'say' market, YES {mid*100:.0f}c; est NO {est_no*100:.1f}%")


PER_MARKET = [mention_fade]


def group_signals(tradable, all_markets, now):
    out = half_draw(tradable, all_markets, now) + goals_poisson(tradable, now, all_markets) \
        + lowscore_spread(tradable, all_markets, now) + crypto_iv(tradable, now)
    counts = {}
    for s in out:
        counts[s.strategy] = counts.get(s.strategy, 0) + 1
    print("  newest-five group signals: " + (", ".join(f"{k} {v}" for k, v in counts.items()) or "none"))
    return out
