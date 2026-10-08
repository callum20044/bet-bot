from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Market:
    ticker: str
    title: str
    event_ticker: str
    yes_bid: Optional[float]   # dollars, 0-1
    yes_ask: Optional[float]
    no_bid: Optional[float]
    no_ask: Optional[float]
    volume_24h: int
    close_time: Optional[datetime]
    status: str
    result: str                # "yes", "no" or "" while unresolved
    expected_time: Optional[datetime] = None   # when the event is actually expected to finish

    @property
    def resolve_time(self) -> Optional[datetime]:
        # Kalshi's close_time is a backstop set days after the event; the expected time is the real one.
        return self.expected_time or self.close_time

    @property
    def yes_mid(self) -> Optional[float]:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return (self.yes_bid + self.yes_ask) / 2

    @property
    def spread(self) -> Optional[float]:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return self.yes_ask - self.yes_bid

    def ask(self, side: str) -> Optional[float]:
        return self.yes_ask if side == "yes" else self.no_ask

    def bid(self, side: str) -> Optional[float]:
        return self.yes_bid if side == "yes" else self.no_bid

    def hours_to_close(self, now: datetime) -> Optional[float]:
        t = self.resolve_time
        if t is None:
            return None
        return (t - now).total_seconds() / 3600


@dataclass
class Signal:
    market: Market
    side: str            # "yes" or "no"
    price: float         # price we'd pay per contract
    est_prob: float      # our estimate that this side wins
    edge: float          # expected profit per contract after fees
    strategy: str
    reason: str
