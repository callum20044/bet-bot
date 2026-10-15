"""Six more strategies (added 15 Oct).

Per-market:
  mid_fade         - NO on YES priced 30-45c: does 'people overpay for YES' carry on past underdogs?
  strong_favourite - YES on 70-85c favourites resolving within a day (the other side of underdog).
Group / consistency (Kalshi's own prices checked against each other):
  ladder_smooth    - in an 'over X' ladder, a rung priced far off the line between its neighbours.
  spread_moneyline - 'X wins by over N' priced from Kalshi's own 'X wins' market.
  field_power      - one-winner fields: favourites are underbet, outsiders overbet; re-weight prices.
  draw_model       - football 'Tie' price vs how evenly matched Kalshi says the two teams are.
"""
import re
from statistics import NormalDist

from . import arb, config
from .fees import fee_per_contract
from .models import Signal
from .overround import is_exclusive, price_for_total


def _edge(est, price):
    return est - price - fee_per_contract(price)


def _hours_ok(m, now, max_h):
    h = m.hours_to_close(now)
    return h is not None and 0 < h <= max_h


def _best(m, p_yes, min_edge, lo=0.03, hi=0.97):
    best = None
    for side, est, price in (("yes", p_yes, m.yes_ask), ("no", 1 - p_yes, m.no_ask)):
        if price is None or not (lo <= price <= hi):
            continue
        e = _edge(est, price)
        if e >= min_edge and (best is None or e > best[3]):
            best = (side, est, price, e)
    return best


# ---------- per-market ----------

def mid_fade(m, now, broker):
    c = config.MID_FADE
    if not c["enabled"] or m.yes_mid is None or not _hours_ok(m, now, c["max_hours_to_close"]):
        return None
    mid = round(m.yes_mid, 4)
    if not (c["yes_min"] < mid <= c["yes_max"]) or m.no_ask is None:
        return None
    est_no = 1 - mid * c["shrink"]
    e = _edge(est_no, m.no_ask)
    if e < c["min_edge"]:
        return None
    return Signal(m, "no", m.no_ask, est_no, e, "mid_fade", f"YES at {mid*100:.0f}c; est NO {est_no*100:.1f}%")


def strong_favourite(m, now, broker):
    c = config.STRONG_FAV
    if not c["enabled"] or m.yes_mid is None or not _hours_ok(m, now, c["max_hours_to_close"]):
        return None
    mid = round(m.yes_mid, 4)
    if not (c["yes_min"] <= mid < c["yes_max"]) or m.yes_ask is None:
        return None
    est = 1 - (1 - mid) * c["shrink"]
    e = _edge(est, m.yes_ask)
    if e < c["min_edge"]:
        return None
    return Signal(m, "yes", m.yes_ask, est, e, "strong_favourite", f"YES at {mid*100:.0f}c; est {est*100:.1f}%")


PER_MARKET = [mid_fade, strong_favourite]


# ---------- groups ----------

def ladder_smooth(tradable, now):
    c = config.LADDER
    if not c["enabled"]:
        return []
    by_event = {}
    for m in tradable:
        r = m.raw or {}
        if r.get("strike_type") == "greater" and r.get("floor_strike") is not None:
            by_event.setdefault(m.event_ticker, []).append(m)
    out = []
    for ms in by_event.values():
        # split into same-question ladders
        groups = []
        for m in ms:
            for g in groups:
                if arb.same_question(g[0], m):
                    g.append(m)
                    break
            else:
                groups.append([m])
        for g in groups:
            if len(g) < c["min_rungs"]:
                continue
            g.sort(key=lambda m: m.raw["floor_strike"])
            # one mispriced rung also throws off its neighbours' comparisons, so only take the
            # single rung furthest off the line in each ladder
            worst = None
            for i in range(1, len(g) - 1):
                lo, m, hi = g[i - 1], g[i], g[i + 1]
                k0, k, k1 = lo.raw["floor_strike"], m.raw["floor_strike"], hi.raw["floor_strike"]
                if not (k0 < k < k1):
                    continue
                line = lo.yes_mid + (hi.yes_mid - lo.yes_mid) * (k - k0) / (k1 - k0)
                d = m.yes_mid - line
                if worst is None or abs(d) > abs(worst[1]):
                    worst = (i, d, line)
            if not worst or abs(worst[1]) < c["min_gap"]:
                continue
            i, d, line = worst
            lo, m, hi = g[i - 1], g[i], g[i + 1]
            if not _hours_ok(m, now, c["max_hours_to_close"]):
                continue
            p = line + c["trust_rung"] * d          # mostly believe the neighbours
            best = _best(m, p, c["min_edge"])
            if best:
                side, est, price, e = best
                out.append(Signal(m, side, price, est, e, "ladder_smooth",
                                  f"over {m.raw['floor_strike']}: {m.yes_mid*100:.0f}c but neighbours ({lo.raw['floor_strike']}: "
                                  f"{lo.yes_mid*100:.0f}c, {hi.raw['floor_strike']}: {hi.yes_mid*100:.0f}c) imply ~{line*100:.0f}c"))
    return out


SPREAD_RE = re.compile(r"^(.*?) wins by over ([\d.]+) points", re.I)


def spread_moneyline(tradable, all_markets, now):
    c = config.SPREAD_ML
    if not c["enabled"]:
        return []
    by_ticker = {m.ticker: m for m in all_markets}
    out = []
    for m in tradable:
        series = m.event_ticker.split("-")[0]
        sd = c["sd"].get(series)
        if sd is None or not _hours_ok(m, now, c["max_hours_to_close"]):
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
        others = [x for t, x in by_ticker.items() if t.startswith(game_ev + "-") and t != mine.ticker
                  and x.yes_mid is not None]
        p_win = mine.yes_mid / (mine.yes_mid + others[0].yes_mid) if len(others) == 1 else mine.yes_mid
        if not (0.05 < p_win < 0.95):
            continue
        mu = sd * NormalDist().inv_cdf(p_win)
        p = 1 - NormalDist(mu, sd).cdf(line)
        best = _best(m, p, c["min_edge"], 0.08, 0.92)
        if best:
            side, est, price, e = best
            out.append(Signal(m, side, price, est, e, "spread_moneyline",
                              f"Kalshi has {code} winning {p_win*100:.0f}% -> by {line}+ ~{p*100:.0f}% vs {m.yes_ask*100:.0f}c"))
    return out


def field_power(tradable, all_markets, now, source, broker, ok):
    """Horse-racing style favourite-longshot bias: true chance ~ price^alpha (alpha > 1), rescaled
    so the field adds up to 100%. Favourites come out a bit better than their price, outsiders worse."""
    c = config.FIELD
    if not c["enabled"] or not hasattr(source, "get_event"):
        return []
    events = {}
    for m in all_markets:
        events.setdefault(m.event_ticker, []).append(m)
    liquid = {m.ticker for m in tradable}
    budget, out = [c["max_lookups"]], []
    for ev, ms in events.items():
        if len(ms) < c["min_outcomes"]:
            continue
        if all((m.raw or {}).get("strike_type") in ("greater", "less") for m in ms):
            continue
        tradeable_here = [m for m in ms if m.ticker in liquid and _hours_ok(m, now, c["max_hours_to_close"])]
        if not tradeable_here:
            continue
        q = {m.ticker: price_for_total(m) for m in ms}
        total = sum(q.values())
        if not (c["min_total"] <= total <= c["max_total"]):
            continue
        if not is_exclusive(source, broker, ev, budget):
            continue
        w = {t: v ** c["alpha"] for t, v in q.items() if v > 0}
        wt = sum(w.values())
        for m in tradeable_here:
            if m.ticker not in w or not (c["price_min"] <= m.yes_mid <= c["price_max"]):
                continue
            p = w[m.ticker] / wt
            best = _best(m, p, c["min_edge"])
            if best:
                side, est, price, e = best
                out.append(Signal(m, side, price, est, e, "field_power",
                                  f"{len(ms)}-way field at {total*100:.0f}%; {m.yes_mid*100:.0f}c -> fair {p*100:.0f}%"))
    return out


def draw_model(tradable, all_markets, now):
    """Football draw chance from how even the match is: about 29% for a coin-flip game,
    falling to ~10% when one side is a heavy favourite (fitted to typical bookmaker prices)."""
    c = config.DRAW_MODEL
    if not c["enabled"]:
        return []
    events = {}
    for m in all_markets:
        events.setdefault(m.event_ticker, []).append(m)
    out = []
    for m in tradable:
        if not m.ticker.endswith("-TIE") or not _hours_ok(m, now, c["max_hours_to_close"]):
            continue
        ms = events.get(m.event_ticker, [])
        teams = [x for x in ms if x is not m and not x.ticker.endswith("-TIE")]
        if len(ms) != 3 or len(teams) != 2 or any(t.yes_mid is None or t.spread is None or t.spread > 0.10 for t in teams):
            continue
        s = teams[0].yes_mid + teams[1].yes_mid + m.yes_mid
        d = abs(teams[0].yes_mid - teams[1].yes_mid) / s
        p = max(c["base"] - c["slope"] * d * d, 0.05)
        best = _best(m, p, c["min_edge"], 0.08, 0.92)
        if best:
            side, est, price, e = best
            out.append(Signal(m, side, price, est, e, "draw_model",
                              f"teams {teams[0].yes_mid*100:.0f}c v {teams[1].yes_mid*100:.0f}c -> draw ~{p*100:.0f}% vs {m.yes_ask*100:.0f}c"))
    return out


def group_signals(tradable, all_markets, now, source, broker, ok):
    out = ladder_smooth(tradable, now) + spread_moneyline(tradable, all_markets, now) \
        + field_power(tradable, all_markets, now, source, broker, ok) + draw_model(tradable, all_markets, now)
    counts = {}
    for s in out:
        counts[s.strategy] = counts.get(s.strategy, 0) + 1
    print("  new-six group signals: " + (", ".join(f"{k} {v}" for k, v in counts.items()) or "none"))
    return out
