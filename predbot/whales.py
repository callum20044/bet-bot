"""Whale follower: copy big bets.

Kalshi publishes every trade, including its size and which side the buyer took. Each scan we
read the most recent few thousand trades (Kalshi does ~10,000 a minute, so that's the last
half-minute or so), pick out the big ones, and follow them - buying the same side, as long as
the price hasn't already run away from what the whale paid.

The assumption under test: someone who puts thousands of dollars on one outcome knows
something, so the true chance is a few cents better than the price they paid.
"""
from . import config
from .fees import fee_per_contract
from .models import Signal


def _f(x, *keys):
    for k in keys:
        v = x.get(k)
        if v not in (None, ""):
            return float(v)
    return None


def parse_trade(t):
    """-> (ticker, side, price of the side bought, dollars) or None."""
    side = t.get("taker_side")
    if side not in ("yes", "no") or t.get("is_block_trade"):
        return None
    count = _f(t, "count_fp", "count")
    if side == "yes":
        price = _f(t, "yes_price_dollars")
        if price is None and t.get("yes_price") is not None:
            price = t["yes_price"] / 100
    else:
        price = _f(t, "no_price_dollars")
        if price is None and t.get("no_price") is not None:
            price = t["no_price"] / 100
    if not count or not price:
        return None
    return t["ticker"], side, price, count * price


def find_whales(trades, c):
    """Big bets, combined per market and side (whales often split one bet into several trades)."""
    agg = {}
    for t in trades:
        p = parse_trade(t)
        if not p:
            continue
        ticker, side, price, usd = p
        if usd < c["min_trade_usd"]:
            continue
        a = agg.setdefault((ticker, side), {"usd": 0.0, "price": 0.0, "n": 0})
        a["usd"] += usd
        a["price"] = max(a["price"], price)
        a["n"] += 1
    return {k: v for k, v in agg.items() if v["usd"] >= c["min_usd"]}


def signals(source, by_ticker, now, broker, ok=lambda m: True):
    c = config.WHALE
    if not c["enabled"] or not hasattr(source, "recent_trades"):
        return []
    try:
        trades = source.recent_trades(c["pages"])
    except Exception as e:
        print(f"  ! whale trades: {e}")
        return []
    whales = find_whales(trades, c)
    out, lookups = [], 0
    for (ticker, side), w in sorted(whales.items(), key=lambda kv: -kv[1]["usd"]):
        if ticker.startswith("KXMVE"):
            continue
        m = by_ticker.get(ticker)
        if m is None and lookups < c["max_lookups"]:
            lookups += 1
            try:
                m = source.get_market(ticker)
            except Exception:
                m = None
        if m is None or not ok(m):
            continue
        hrs = m.hours_to_close(now)
        if hrs is None or not (0 < hrs <= c["max_hours_to_close"]):
            continue
        price = m.ask(side)
        if price is None or not (c["price_min"] <= w["price"] <= c["price_max"]):
            continue
        if price > w["price"] + c["max_chase"]:
            continue                      # the price has already run away
        est = min(w["price"] + c["nudge"], 0.99)
        edge = est - price - fee_per_contract(price)
        if edge < c["min_edge"]:
            continue
        out.append(Signal(m, side, price, est, edge, "whale",
                          f"${w['usd']:,.0f} on {side.upper()} @ {w['price']*100:.0f}c"
                          + (f" ({w['n']} trades)" if w["n"] > 1 else "")))
    return out
