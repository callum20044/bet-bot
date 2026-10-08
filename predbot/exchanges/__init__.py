"""Market data sources. Anything with these methods can drive the bot:

    now() -> datetime (UTC)
    list_open_markets() -> list[Market]
    get_market(ticker) -> Market
    fee(contracts, price) -> float

Kalshi (live, public data) and an offline simulator are implemented. Betfair is a stub
ready to be filled in once paper results justify real money.
"""
