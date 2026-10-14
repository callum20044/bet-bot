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


def signals(source, markets, now, broker, ok=lambda m: True):
    c = config.OVERROUND
    if not c["enabled"] or not hasattr(source, "get_event"):
        return []
    by_event = {}
    for m in markets:
        by_event.setdefault(m.event_ticker, []).append(m)
    budget, out = [c["max_lookups"]], []
    for ev, ms in by_event.items():
        if len(ms) < c["min_outcomes"] or any(m.yes_mid is None for m in ms):
            continue
        total = sum(m.yes_mid for m in ms)
        if not (c["min_overround"] <= total <= c["max_overround"]):
            continue
        if not any(ok(m) and c["yes_min"] <= m.yes_mid <= c["yes_max"] for m in ms):
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
            if not (c["yes_min"] <= mid <= c["yes_max"]) or m.no_ask is None:
                continue
            est_no = 1 - mid / total
            edge = est_no - m.no_ask - fee_per_contract(m.no_ask)
            if edge >= c["min_edge"]:
                out.append(Signal(m, "no", m.no_ask, est_no, edge, "overround",
                                  f"{len(ms)} outcomes priced at {total*100:.0f}% in total; YES {mid*100:.0f}c -> fair {mid/total*100:.0f}%"))
    return out
