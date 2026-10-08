"""Rules-based strategies. Each looks at a market and either returns a Signal or None.

A signal's `edge` is expected profit per contract after fees, given the strategy's own
estimate of the true probability. That estimate is the strategy's *assumption*; the paper-
trading scoreboard is how you find out whether the assumption is any good."""
from . import config
from .fees import fee_per_contract
from .models import Signal


def _edge(est_prob, price):
    return est_prob - price - fee_per_contract(price)


def longshot_fade(m, now, broker):
    c = config.LONGSHOT
    if not c["enabled"] or m.yes_mid is None:
        return None
    hrs = m.hours_to_close(now)
    if hrs is None or not (0 < hrs <= c["max_hours_to_close"]):
        return None
    mid = round(m.yes_mid, 4)   # avoid 0.15000000000000002 falling outside a 15c limit
    if not (c["yes_min"] <= mid <= c["yes_max"]):
        return None
    price = m.no_ask
    if price is None:
        return None
    est_no = 1 - mid * c["shrink"]
    edge = _edge(est_no, price)
    if edge < c["min_edge"]:
        return None
    return Signal(m, "no", price, est_no, edge, "longshot_fade",
                  f"YES at {m.yes_mid*100:.0f}c, {hrs:.0f}h to close; est NO {est_no*100:.1f}%")


def momentum(m, now, broker):
    c = config.MOMENTUM
    if not c["enabled"] or m.yes_mid is None:
        return None
    hrs = m.hours_to_close(now)
    if hrs is None or not (0 < hrs <= c["max_hours_to_close"]):
        return None
    if not (c["price_min"] <= round(m.yes_mid, 4) <= c["price_max"]):
        return None
    before = broker.price_ago(m.ticker, now, c["lookback_minutes"])
    if before is None:
        return None
    move = m.yes_mid - before
    if abs(move) < c["min_move"]:
        return None
    side = "yes" if move > 0 else "no"
    p_yes = min(max(m.yes_mid + c["follow_through"] * move, 0.01), 0.99)
    est = p_yes if side == "yes" else 1 - p_yes
    price = m.ask(side)
    if price is None:
        return None
    edge = _edge(est, price)
    if edge < c["min_edge"]:
        return None
    return Signal(m, side, price, est, edge, "momentum",
                  f"moved {move*100:+.0f}c in {c['lookback_minutes']}m ({before*100:.0f}c -> {m.yes_mid*100:.0f}c)")


ALL = [longshot_fade, momentum]
