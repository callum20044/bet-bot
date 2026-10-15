"""Check a grid bot and 'buy the dip' plans on real Bitcoin / Ethereum history (Coinbase prices).

All amounts in pounds-equivalent; exchange fee 0.4% per trade (typical for small accounts).
"""
import json
import math
import time
import urllib.request
from datetime import datetime, timezone

UA = {"User-Agent": "Mozilla/5.0 (crypto check)"}
FEE = 0.004


def get(url):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.load(r)
        except Exception:
            time.sleep(2 + i * 3)
    raise RuntimeError(url)


def candles(product, gran, start_ts, end_ts):
    """[(ts, close)] oldest first. Coinbase returns max 300 per call."""
    out, t = {}, start_ts
    step = gran * 300
    while t < end_ts:
        t2 = min(t + step, end_ts)
        s = datetime.fromtimestamp(t, timezone.utc).isoformat()
        e = datetime.fromtimestamp(t2, timezone.utc).isoformat()
        rows = get(f"https://api.exchange.coinbase.com/products/{product}/candles?granularity={gran}&start={s}&end={e}")
        for r in rows:
            out[r[0]] = r[4]
        t = t2
        time.sleep(0.25)
    return sorted(out.items())


# ------------------------------------------------------------------ grid bot

def grid(prices, spacing, levels, recenter, capital=1000.0):
    """Buy one step each time the price falls a full level below the last trade; sell one step
    each time it rises a full level above it. Each round trip earns one level's spacing (minus fees)."""
    p0 = prices[0][1]
    unit = capital / (2 * levels)
    coin = (capital / 2) / p0 * (1 - FEE)
    cash = capital / 2
    last = 0                                  # level of the most recent trade (0 = start price)
    outside = trades = 0
    lvl = lambda k: p0 * (1 + spacing) ** k
    for ts, p in prices:
        if p < lvl(-levels) or p > lvl(levels):
            outside += 1
            if recenter and outside >= 72:    # 3 days outside the grid: rebuild it around today's price
                value = cash + coin * p
                target = (value / 2) / p
                cash = value / 2 - abs(target - coin) * p * FEE
                coin, p0, last, outside = target, p, 0, 0
                trades += 1
                continue
        else:
            outside = 0
        while last > -levels and p <= lvl(last - 1):      # fell a full level: buy one step
            if cash >= unit:
                coin += unit / p * (1 - FEE)
                cash -= unit
                trades += 1
            last -= 1
        while last < levels and p >= lvl(last + 1):       # rose a full level: sell one step
            q = unit / p
            if coin >= q:
                coin -= q
                cash += unit * (1 - FEE)
                trades += 1
            last += 1
    return cash + coin * prices[-1][1], trades


# ------------------------------------------------------------------ buy the dip

def fear_greed():
    data = get("https://api.alternative.me/fng/?limit=0&format=json")["data"]
    return {datetime.fromtimestamp(int(d["timestamp"]), timezone.utc).date(): int(d["value"]) for d in data}


def dca(daily, fng, plan, weekly=50.0):
    cash_in = coin = 0.0
    hist = []
    for i, (ts, p) in enumerate(daily):
        hist.append(p)
        day = datetime.fromtimestamp(ts, timezone.utc).date()
        if day.weekday() != 0:          # Mondays
            continue
        amt = weekly
        if plan == "dip":
            high = max(hist[-90:])
            if p < high * 0.80:
                amt += 2 * weekly        # 20%+ below the 90-day high: triple the buy
        elif plan in ("fear", "fear_sell"):
            f = fng.get(day)
            if f is None:
                continue
            amt = weekly * (2.0 if f < 25 else 1.5 if f < 45 else 1.0 if f <= 55 else 0.75 if f <= 75 else 0.5)
            if plan == "fear_sell" and f > 85 and coin > 0:
                sell = coin * 0.10
                coin -= sell
                cash_in -= sell * p * (1 - FEE)          # take 10% of holdings off the table
        coin += amt / p * (1 - FEE)
        cash_in += amt
    end = daily[-1][1]
    value = coin * end
    return cash_in, value


def main():
    now = int(time.time())
    out = ["# Crypto bot check", "", f"Run {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. Fees 0.4% per trade.", ""]
    fng = fear_greed()
    for product in ("BTC-USD", "ETH-USD"):
        hourly = candles(product, 3600, now - 730 * 86400, now)
        daily = candles(product, 86400, int(datetime(2018, 2, 1, tzinfo=timezone.utc).timestamp()), now)
        out.append(f"## {product}")
        for label, series in (("last 2 years", hourly), ("last 6 months", hourly[-4380:])):
            p0, p1 = series[0][1], series[-1][1]
            out.append(f"### Grid bot, {label} (price {p0:,.0f} -> {p1:,.0f})")
            out.append(f"- just holding £1,000 of {product[:3]}: **£{1000 * p1 / p0:,.0f}**; half cash / half coin: £{500 + 500 * p1 / p0:,.0f}")
            for spacing in (0.01, 0.02, 0.04):
                for rec in (False, True):
                    val, n = grid(series, spacing, 10, rec)
                    out.append(f"- {spacing*100:.0f}% steps, 10 levels each way, {'re-centres' if rec else 'fixed'}: "
                               f"**£{val:,.0f}** ({n} trades)")
        for label, series in (("since Feb 2018", daily), ("last 2 years", daily[-730:])):
            out.append(f"### Weekly buying, {label}")
            for plan, desc in (("plain", "£50 every Monday"), ("dip", "£50, tripled when 20%+ below 90-day high"),
                               ("fear", "£25-£100 depending on fear & greed index"),
                               ("fear_sell", "fear & greed buying + sell 10% when extreme greed")):
                put_in, val = dca(series, fng, plan)
                out.append(f"- {desc}: put in £{put_in:,.0f}, worth **£{val:,.0f}** ({(val / put_in - 1) * 100:+.0f}%)")
        out.append("")
    text = "\n".join(out)
    print(text)
    open("crypto_results.md", "w").write(text + "\n")


if __name__ == "__main__":
    main()
