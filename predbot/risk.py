"""Hard limits that every trade must pass, no matter how confident a strategy is."""
import math

from . import config


def market_ok(m):
    """Basic tradability filter applied before strategies even look."""
    return (m.yes_mid is not None and m.spread is not None and m.spread <= config.MAX_SPREAD
            and m.volume_24h >= config.MIN_VOLUME_24H)


def size_and_check(sig, broker, now, open_tickers, open_events, n_open, open_by_strategy=None):
    """Return (contracts, "") to trade, or (0, reason) if blocked."""
    if n_open >= config.MAX_OPEN_POSITIONS:
        return 0, "max open positions"
    if (open_by_strategy or {}).get(sig.strategy, 0) >= config.MAX_OPEN_PER_STRATEGY:
        return 0, "strategy cap"
    if sig.market.ticker in open_tickers:
        return 0, "already holding"
    if open_events.get(sig.market.event_ticker, 0) >= config.MAX_PER_EVENT:
        return 0, "event cap"
    if not (config.MIN_PRICE <= sig.price <= config.MAX_PRICE):
        return 0, "price out of range"
    if broker.realized_today(now) <= -config.DAILY_LOSS_LIMIT:
        return 0, "daily loss limit hit"
    n = min(config.MAX_CONTRACTS, math.floor(config.STAKE_PER_TRADE / sig.price))
    if n < 1:
        return 0, "stake too small"
    return n, ""
