"""Overround fade: 'who will win?' markets where the YES prices add up to well over 100%.

Only one candidate can win, so the true chances add up to (at most) 100%. When the prices add
up to, say, 120%, every candidate is overpriced by about a sixth - the same 'people overpay for
YES' habit the longshot and favourites strategies profit from. We bet NO on the mid-priced
candidates, valuing each at its price scaled back down to a 100% total.
"""
from . import config
from .fees import fee_per_contract
from .models import Signal


def is_exclusive(source, broker, event_ticker, budget):
    """Kalshi's own 'only one of these can happen' flag, cached in the database."""
    key = f"me:{event_ticker}"
    row = broker.db.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    if row:
        return row[0] == "1"
    if budget[0] <= 0:
        return None
    budget[0] -= 1
    try:
        ev = source.get_event(event_ticker)
    except Exception as e:
        print(f"  ! event {event_ticker}: {e}")
        return None
    flag = bool(ev.get("mutually_exclusive"))
    broker.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, "1" if flag else "0"))
    broker.db.commit()
    return flag


def price_for_total(m):
    """Mid if the market has a real two-sided price; otherwise its bid (an empty book shows as
    bid 0 / ask $1, which is not a 50c chance)."""
    if m.yes_mid is not None and m.yes_ask - m.yes_bid <= 0.10:
        return m.yes_mid
    return m.yes_bid or 0


def signals(source, markets, now, broker, ok=lambda m: True):
    c = config.OVERROUND
    if not c["enabled"] or not hasattr(source, "get_event"):
        return []
    by_event = {}
    for m in markets:
        by_event.setdefault(m.event_ticker, []).append(m)
    budget, out, seen = [c["max_lookups"]], [], []
    for ev, ms in by_event.items():
        if len(ms) < c["min_outcomes"]:
            continue
        # quiet candidates with no two-sided price count at their bid (or 0): keeps the total,
        # and so our edge, on the cautious side
        total = sum(price_for_total(m) for m in ms)
        seen.append((total, ev, len(ms)))
        if not (c["min_overround"] <= total <= c["max_overround"]):
            continue
        if all((m.raw or {}).get("strike_type") in ("greater", "less") for m in ms):
            continue          # 'over X' ladders: several can all come true
        if not any(ok(m) and m.yes_mid is not None and c["yes_min"] <= m.yes_mid <= c["yes_max"] for m in ms):
            continue
        if not is_exclusive(source, broker, ev, budget):
            continue
        for m in ms:
            if not ok(m):
                continue
            hrs = m.hours_to_close(now)
            if hrs is None or not (0 < hrs <= c["max_hours_to_close"]):
                continue
            mid = m.yes_mid
            if mid is None or not (c["yes_min"] <= mid <= c["yes_max"]) or m.no_ask is None:
                continue
            est_no = 1 - mid / total
            edge = est_no - m.no_ask - fee_per_contract(m.no_ask)
            if edge >= c["min_edge"]:
                out.append(Signal(m, "no", m.no_ask, est_no, edge, "overround",
                                  f"{len(ms)} outcomes priced at {total*100:.0f}% in total; YES {mid*100:.0f}c -> fair {mid/total*100:.0f}%"))
    band = sorted(x for x in seen if 1.0 <= x[0] <= 2.0)[::-1]
    print(f"  overround: {len(seen)} multi-outcome events, {len(band)} priced 100-200%: "
          + ", ".join(f"{e}({n})={t*100:.0f}%" for t, e, n in band[:12]))
    return out
