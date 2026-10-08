"""Offline fake market for testing the plumbing without internet.

Each scan advances a simulated clock by 10 minutes. Markets have a hidden "true" probability,
the quoted price deliberately overprices longshots (like real prediction markets tend to), and
markets resolve randomly according to their true probability. Results here say nothing about
real-world profitability - it's only for checking the bot works.
"""
import math
import random
from datetime import datetime, timedelta, timezone

from ..fees import kalshi_taker_fee
from ..models import Market

STEP = timedelta(minutes=10)


class SimulatedData:
    name = "simulated"

    def __init__(self, seed=42, n_markets=120):
        self.rng = random.Random(seed)
        self.clock = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.markets = {}
        self.n_markets = n_markets
        self.counter = 0
        for _ in range(n_markets):
            self._new_market()

    def now(self):
        return self.clock

    def fee(self, contracts, price):
        return kalshi_taker_fee(contracts, price)

    def _new_market(self):
        self.counter += 1
        r = self.rng.random()
        p = self.rng.uniform(0.01, 0.12) if r < 0.4 else self.rng.uniform(0.1, 0.9)
        t = f"SIM-{self.counter:05d}"
        self.markets[t] = dict(
            ticker=t, event=f"SIMEV-{self.counter:05d}", p=p,
            close=self.clock + timedelta(hours=self.rng.uniform(2, 120)),
            vol=self.rng.randint(50, 20000), result="", status="open",
        )

    def _quote(self, s):
        p = s["p"]
        mid = p / 0.75 if p < 0.15 else p          # longshot bias baked in
        mid = min(max(mid + self.rng.gauss(0, 0.01), 0.02), 0.98)
        half = self.rng.choice([0.005, 0.01, 0.015, 0.03])
        yb, ya = round(max(mid - half, 0.01), 2), round(min(mid + half, 0.99), 2)
        if ya <= yb:
            ya = round(yb + 0.01, 2)
        return yb, ya

    def _to_market(self, s):
        yb, ya = (None, None) if s["status"] != "open" else self._quote(s)
        return Market(
            ticker=s["ticker"], title=f"Simulated question {s['ticker']}", event_ticker=s["event"],
            yes_bid=yb, yes_ask=ya,
            no_bid=None if ya is None else round(1 - ya, 2),
            no_ask=None if yb is None else round(1 - yb, 2),
            volume_24h=s["vol"], close_time=s["close"], status=s["status"], result=s["result"],
        )

    def _advance(self):
        self.clock += STEP
        for s in list(self.markets.values()):
            if s["status"] != "open":
                continue
            # Fair random walk in log-odds space: the price is an unbiased guess, with rare news jumps.
            lo = math.log(s["p"] / (1 - s["p"]))
            lo += self.rng.gauss(0, 0.6) if self.rng.random() < 0.003 else self.rng.gauss(0, 0.03)
            s["p"] = 1 / (1 + math.exp(-lo))
            if self.clock >= s["close"]:
                s["status"] = "finalized"
                s["result"] = "yes" if self.rng.random() < s["p"] else "no"
                self._new_market()

    def list_open_markets(self):
        self._advance()
        return [self._to_market(s) for s in self.markets.values() if s["status"] == "open"]

    def get_market(self, ticker):
        return self._to_market(self.markets[ticker])
