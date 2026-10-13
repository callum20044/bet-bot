"""Crypto clock: Kalshi's hourly Bitcoin / Ethereum price markets vs the live price.

Kalshi asks "will BTC be above $X at 4pm?" (and price ranges) every hour. They settle on the
average price in the last minute. We take the live price from Coinbase, measure how much it's
been moving minute to minute over the last two hours, and work out the odds of finishing above
each line with the standard options-pricing assumption (random walk in log-price).
"""
import json
import math
import urllib.request

from . import config
from .fees import fee_per_contract
from .models import Signal

COINBASE = "https://api.exchange.coinbase.com/products/{}/candles?granularity=60"
SERIES = {"KXBTCD": "BTC-USD", "KXBTC": "BTC-USD", "KXETHD": "ETH-USD", "KXETH": "ETH-USD"}


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def candles(product):
    req = urllib.request.Request(COINBASE.format(product), headers={"User-Agent": "predbot"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)          # [[time, low, high, open, close, volume], ...] newest first


def spot_and_vol(rows, minutes=120):
    closes = [r[4] for r in rows[: minutes + 1]]
    rets = [math.log(closes[i] / closes[i + 1]) for i in range(len(closes) - 1)]
    m = sum(rets) / len(rets)
    sd = math.sqrt(sum((x - m) ** 2 for x in rets) / (len(rets) - 1))
    return closes[0], sd


def prob(raw, spot, sd_per_min, minutes):
    """P(YES) for a Kalshi greater / less / between market."""
    s = max(sd_per_min * math.sqrt(max(minutes, 0.5)), 1e-6)
    above = lambda k: 1 - norm_cdf(math.log(k / spot) / s)
    st = raw.get("strike_type")
    if st == "greater":
        return above(raw["floor_strike"])
    if st == "less":
        return 1 - above(raw["cap_strike"])
    if st == "between":
        return above(raw["floor_strike"]) - above(raw["cap_strike"])
    return None


def signals(markets, now, broker, fetch=candles):
    c = config.CRYPTO
    if not c["enabled"]:
        return []
    feeds, out = {}, []
    for m in markets:
        series = m.event_ticker.split("-")[0]
        product = SERIES.get(series)
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
                feeds[product] = spot_and_vol(fetch(product), c["vol_minutes"])
            except Exception as e:
                print(f"  ! coinbase {product}: {e}")
                feeds[product] = None
        if not feeds[product]:
            continue
        spot, sd = feeds[product]
        try:
            p = prob(m.raw, spot, sd * c["vol_scale"], mins)
        except (KeyError, TypeError, ValueError):
            continue
        if p is None:
            continue
        p = min(max(p, 0.005), 0.995)
        best = None
        for side, pr, price in (("yes", p, m.yes_ask), ("no", 1 - p, m.no_ask)):
            if price is None or not (c["price_min"] <= price <= c["price_max"]):
                continue
            edge = pr - price - fee_per_contract(price)
            if edge >= c["min_edge"] and (best is None or edge > best[3]):
                best = (side, pr, price, edge)
        if best:
            side, pr, price, edge = best
            out.append(Signal(m, side, price, pr, edge, "crypto",
                              f"{product[:3]} ${spot:,.0f}, {mins:.0f}m left, model YES {p*100:.0f}% vs {m.yes_ask*100:.0f}c"))
    return out
