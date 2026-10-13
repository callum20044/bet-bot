"""Broken-maths hunter: groups of Kalshi markets whose prices can't all be right.

1. Complete sets - every outcome of an event is listed exactly once (a football match's
   home / away / tie, or a full ladder of temperature brackets). Exactly one pays $1, so:
     * if the YES prices add up to less than $1, buying every YES locks in a profit;
     * if the YES bids add up to more than $1, buying every NO locks one in.
2. Ladders - "above 54.5" can never be less likely than "above 59.5". If the higher line's
   YES is bid above the lower line's YES ask, buy YES low + NO high: at least $1 back, always.

Every leg pays Kalshi's fee, so the gap has to beat the fees before we take it.
"""
import math
import re

from . import config
from .fees import kalshi_taker_fee


class Basket:
    def __init__(self, legs, payout, kind, note):
        self.legs = legs            # [(market, side, price)]
        self.payout = payout        # guaranteed $ per 1 contract of each leg
        self.kind = kind
        self.note = note

    def cost_per_set(self):
        return sum(p for _, _, p in self.legs)

    def profit(self, n):
        fees = sum(kalshi_taker_fee(n, p) for _, _, p in self.legs)
        return n * self.payout - n * self.cost_per_set() - fees


def _game_event(ms):
    """Head-to-head game with every outcome listed (2 teams, or 2 teams + tie)."""
    if not ms or "GAME" not in ms[0].event_ticker.split("-")[0]:
        return False
    ties = [m for m in ms if m.ticker.endswith("-TIE")]
    return (len(ms) == 2 and not ties) or (len(ms) == 3 and len(ties) == 1)


def _bracket_set(ms):
    """Ranges that tile the whole number line: one 'less', contiguous 'between's, one 'greater'."""
    raw = [(m, m.raw or {}) for m in ms]
    less = [x for x in raw if x[1].get("strike_type") == "less"]
    great = [x for x in raw if x[1].get("strike_type") == "greater"]
    btw = sorted([x for x in raw if x[1].get("strike_type") == "between"], key=lambda x: x[1]["floor_strike"])
    if len(less) != 1 or len(great) != 1 or not btw or len(less) + len(great) + len(btw) != len(ms):
        return False
    edges = [less[0][1].get("cap_strike")]
    for _, r in btw:
        if r.get("floor_strike") is None or r.get("cap_strike") is None:
            return False
        edges += [r["floor_strike"], r["cap_strike"]]
    edges.append(great[0][1].get("floor_strike"))
    if None in edges:
        return False
    # between brackets are whole-number inclusive ranges: [lo, hi], next starts at hi+1
    step = 1.0
    if not math.isclose(edges[1], edges[0], abs_tol=1e-6):          # 'less than X' then first bracket starts at X
        return False
    for i in range(2, len(edges) - 2, 2):
        if not math.isclose(edges[i + 1], edges[i] + step, abs_tol=1e-6):   # next floor = previous cap + 1
            return False
    return math.isclose(edges[-1], edges[-2], abs_tol=1e-6)        # 'greater than X' where X = last cap


def same_question(a, b):
    """True if two markets ask the same thing apart from the number."""
    strip = lambda m: re.sub(r"[\d.,$%]+", "#", m.title)
    return strip(a) == strip(b)


def find(markets, all_markets=None):
    """`markets` are the liquid ones we may trade; `all_markets` is everything scanned, used to
    check an event really has no other outcomes (a quiet 'Tie' market still counts)."""
    c = config.ARB
    by_event, full = {}, {}
    for m in markets:
        by_event.setdefault(m.event_ticker, []).append(m)
    for m in (all_markets if all_markets is not None else markets):
        full.setdefault(m.event_ticker, set()).add(m.ticker)
    out = []
    for ev, ms in by_event.items():
        if any(m.yes_ask is None or m.yes_bid is None for m in ms):
            continue
        whole = {m.ticker for m in ms} == full.get(ev, set())
        complete = whole and (_game_event(ms) or _bracket_set(ms))
        if complete and len(ms) >= 2:
            asks = sum(m.yes_ask for m in ms)
            if asks < 1:
                out.append(Basket([(m, "yes", m.yes_ask) for m in ms], 1.0, "set",
                                  f"all {len(ms)} outcomes' YES cost {asks*100:.0f}c, one pays $1"))
            bids = sum(m.yes_bid for m in ms)
            if bids > 1 and all(m.no_ask is not None for m in ms):
                out.append(Basket([(m, "no", m.no_ask) for m in ms], float(len(ms) - 1), "set",
                                  f"YES bids add to {bids*100:.0f}c > $1: all NOs pay ${len(ms)-1}"))
        # ladders: "above X" markets in the same event
        ladder = sorted([m for m in ms if (m.raw or {}).get("strike_type") == "greater"
                         and (m.raw or {}).get("floor_strike") is not None],
                        key=lambda m: m.raw["floor_strike"])
        for i, lo in enumerate(ladder):
            for hi in ladder[i + 1:]:
                # only true ladders: same question, higher line (e.g. 'above 54.5' vs 'above 59.5'),
                # never different cities/teams that happen to share an event and a strike of 0
                if not (hi.raw["floor_strike"] > lo.raw["floor_strike"] and same_question(lo, hi)):
                    continue
                if hi.yes_bid is not None and lo.yes_ask is not None and hi.no_ask is not None \
                        and hi.yes_bid > lo.yes_ask:
                    out.append(Basket([(lo, "yes", lo.yes_ask), (hi, "no", hi.no_ask)], 1.0, "ladder",
                                      f"above {lo.raw['floor_strike']} @ {lo.yes_ask*100:.0f}c is cheaper than "
                                      f"above {hi.raw['floor_strike']} @ {hi.yes_bid*100:.0f}c"))
    good = []
    for b in out:
        n = max(1, math.floor(c["stake"] / max(b.cost_per_set(), 0.01)))
        n = min(n, config.MAX_CONTRACTS)
        if b.profit(n) / n >= c["min_profit_per_set"]:
            b.n = n
            good.append(b)
    good.sort(key=lambda b: -b.profit(b.n))
    return good
