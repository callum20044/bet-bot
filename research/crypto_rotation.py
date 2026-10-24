"""Can a bot that may buy ANY coin on Coinbase beat (a) holding Bitcoin and (b) picking coins at random?

Universe: every Coinbase X-USD market (including delisted ones still in the product list, to limit
survivorship bias), minus stablecoins. A coin is only eligible on a date if it was trading then and its
30-day average daily volume was at least $1M (so the bot can actually buy it).

Rules tested (rebalanced every 7 days, equal weight, cost 0.8% per buy/sell = 0.6% fee + 0.2% slippage):
  momentum  - hold the top N coins by return over the last L days
  reversal  - hold the N worst coins over the last L days (buy the losers)
  each with / without a "regime filter": only invested while Bitcoin is above its 50-day average, else cash
Benchmarks: hold Bitcoin; hold top-20 by volume equal weight.
Luck check: 200 runs that pick N eligible coins at random on the same dates (same filter, same costs).
Split: first 60% of the history = "seen", last 40% = "unseen". Only the unseen part counts.
"""
import json
import math
import random
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
import pandas as pd

UA = {"User-Agent": "Mozilla/5.0 (crypto rotation check)"}
COST = 0.008
DAYS = 1100
MIN_VOL = 1_000_000
STABLE = {"USDT", "USDC", "DAI", "PYUSD", "EURC", "GUSD", "PAX", "BUSD", "UST", "TUSD", "USDS", "GYEN", "RLUSD", "FDUSD", "USD1"}


def get(url):
    for i in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.load(r)
        except Exception:
            time.sleep(1 + i * 2)
    return None


def history(pid):
    end = int(time.time())
    start = end - DAYS * 86400
    rows, t = {}, start
    while t < end:
        t2 = min(t + 300 * 86400, end)
        s = datetime.fromtimestamp(t, timezone.utc).isoformat()
        e = datetime.fromtimestamp(t2, timezone.utc).isoformat()
        data = get(f"https://api.exchange.coinbase.com/products/{pid}/candles?granularity=86400&start={s}&end={e}") or []
        if isinstance(data, list):
            for r in data:
                rows[r[0]] = (r[4], r[4] * r[5])
        t = t2
        time.sleep(0.15)
    if not rows:
        return None
    idx = pd.to_datetime(sorted(rows), unit="s", utc=True).normalize()
    vals = [rows[k] for k in sorted(rows)]
    return pd.DataFrame(vals, index=idx, columns=["close", "dollar_vol"])


def load():
    prods = get("https://api.exchange.coinbase.com/products") or []
    ids = sorted({p["id"] for p in prods if p.get("quote_currency") == "USD" and p.get("base_currency") not in STABLE})
    print(f"{len(ids)} USD coins on Coinbase")
    with ThreadPoolExecutor(6) as ex:
        res = dict(zip(ids, ex.map(history, ids)))
    res = {k: v for k, v in res.items() if v is not None and len(v) > 40}
    close = pd.DataFrame({k: v["close"] for k, v in res.items()}).sort_index()
    vol = pd.DataFrame({k: v["dollar_vol"] for k, v in res.items()}).sort_index()
    print(f"{close.shape[1]} coins with history, {close.shape[0]} days")
    return close, vol


def simulate(close, picks_fn, regime, btc_ma):
    """Weekly rebalance. picks_fn(date_i) -> list of coins. Returns daily equity series."""
    dates = close.index
    rets = close.pct_change(fill_method=None)
    eq, cur, out = 1.0, set(), []
    for i in range(len(dates)):
        if i > 0 and cur:
            r = rets.iloc[i][list(cur)].fillna(-1.0 if False else 0.0)
            eq *= 1 + r.mean()
        if i % 7 == 0 and i >= 60:
            want = set(picks_fn(i)) if (not regime or btc_ma.iloc[i]) else set()
            # selling dropped coins and buying new ones: each changed slot costs one sell + one buy
            changed = len(cur - want) / max(len(cur), 1) if cur else 0
            added = len(want - cur) / max(len(want), 1) if want else 0
            eq *= (1 - COST * changed) * (1 - COST * added) if (cur or want) else 1
            cur = want
        out.append(eq)
    return pd.Series(out, index=dates)


def stats(eq):
    r = eq.pct_change().fillna(0)
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 0.01)
    sd = r.std()
    return dict(ret=float(eq.iloc[-1] / eq.iloc[0] - 1), cagr=float((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1),
                sharpe=float(r.mean() / sd * math.sqrt(365)) if sd > 0 else 0.0,
                maxdd=float(1 - (eq / eq.cummax()).min()))


def main():
    close, vol = load()
    eligible = (vol.rolling(30, min_periods=20).mean() >= MIN_VOL) & close.notna()
    btc = close["BTC-USD"]
    btc_ma = (btc > btc.rolling(50).mean()).fillna(False)
    split = close.index[int(len(close) * 0.6)]
    halves = {"seen": (close.index[0], split), "unseen": (split, close.index[-1])}

    def part(eq, which):
        a, b = halves[which]
        e = eq[(eq.index >= a) & (eq.index <= b)]
        return stats(e)

    rows = []
    for L in (7, 14, 30, 60):
        lb = close / close.shift(L) - 1
        for N in (3, 5, 10):
            for mode in ("momentum", "reversal"):
                def pick(i, lb=lb, N=N, mode=mode):
                    s = lb.iloc[i][eligible.iloc[i]].dropna()
                    s = s.sort_values(ascending=(mode == "reversal"))
                    return list(s.index[:N])
                for regime in (False, True):
                    eq = simulate(close, pick, regime, btc_ma)
                    rows.append(dict(rule=f"{mode} top{N} {L}d" + (" +BTC filter" if regime else ""), mode=mode, N=N, L=L,
                                     regime=regime, seen=part(eq, "seen"), unseen=part(eq, "unseen"), eq=eq))
    # benchmarks
    hold = (btc / btc.iloc[0]).ffill()
    top20 = simulate(close, lambda i: list(vol.iloc[i - 30:i].mean()[eligible.iloc[i]].sort_values(ascending=False).index[:20]), False, btc_ma)
    # luck: random picks
    luck = {}
    for N in (3, 5, 10):
        for regime in (False, True):
            res = []
            for seed in range(100):
                rnd = random.Random(seed)
                def pick(i, N=N, rnd=rnd):
                    pool = list(eligible.columns[eligible.iloc[i].values])
                    return rnd.sample(pool, min(N, len(pool)))
                res.append(part(simulate(close, pick, regime, btc_ma), "unseen")["ret"])
            luck[(N, regime)] = sorted(res)
    out = ["# Free-roaming crypto bot: backtest", "",
           f"Run {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. {close.shape[1]} Coinbase coins, {close.index[0]:%Y-%m-%d} to "
           f"{close.index[-1]:%Y-%m-%d}. Unseen period starts {split:%Y-%m-%d}. Weekly rebalance, 0.8% cost per buy/sell, "
           f"coins need $1M+/day volume.", "",
           "## Benchmarks (unseen period)",
           f"- Hold Bitcoin: {part(hold, 'unseen')['ret']*100:+.0f}% (worst drop -{part(hold, 'unseen')['maxdd']*100:.0f}%)",
           f"- Top 20 coins by volume, equal weight: {part(top20, 'unseen')['ret']*100:+.0f}% (worst drop -{part(top20, 'unseen')['maxdd']*100:.0f}%)", "",
           "## Random picking (luck check, 100 runs each, unseen period)",
           "| Coins held | BTC filter | Random median | Random best 10% |", "|---|---|---|---|"]
    for (N, regime), v in luck.items():
        out.append(f"| {N} | {'yes' if regime else 'no'} | {np.median(v)*100:+.0f}% | {np.percentile(v, 90)*100:+.0f}% |")
    out += ["", "## Every rule (sorted by unseen return)",
            "| Rule | Seen return | Unseen return | Unseen Sharpe | Unseen worst drop | Beats % of random |", "|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: -r["unseen"]["ret"]):
        lk = luck[(r["N"], r["regime"])]
        pct = sum(x < r["unseen"]["ret"] for x in lk) / len(lk) * 100
        r["beats"] = pct
        out.append(f"| {r['rule']} | {r['seen']['ret']*100:+.0f}% | {r['unseen']['ret']*100:+.0f}% | {r['unseen']['sharpe']:.2f} | "
                   f"-{r['unseen']['maxdd']*100:.0f}% | {pct:.0f}% |")
    good = [r for r in rows if r["seen"]["ret"] > 0 and r["unseen"]["ret"] > 0 and r["beats"] >= 90]
    out += ["", f"**Rules that made money in BOTH periods and beat 90%+ of random pickers: {len(good)} of {len(rows)}**",
            ", ".join(r["rule"] for r in good) or "none"]
    open("crypto_rotation.md", "w").write("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
