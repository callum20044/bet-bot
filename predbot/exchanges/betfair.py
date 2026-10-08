"""Betfair Exchange adapter - NOT BUILT YET (deliberately).

Plan for when paper trading has shown an edge:
  1. Betfair account + an Application Key (developer.betfair.com) + a self-signed SSL cert for
     non-interactive login.
  2. `pip install betfairlightweight` and implement the same methods the bot uses:
       now(), list_open_markets(), get_market(ticker), fee(contracts, price)
     mapping Betfair runners to Market objects (back price -> 1/odds as the "yes" price).
  3. Betfair charges commission on net winnings per market (typically 5% in the UK), not a
     per-contract fee - fee() should return 0 at entry and commission be applied at settlement.
  4. Add a real order-placing broker alongside PaperBroker, with the same risk limits, and run it
     with tiny stakes first.
"""


class BetfairData:
    name = "betfair"

    def __init__(self, *a, **k):
        raise NotImplementedError("Betfair adapter isn't built yet - see notes at the top of this file.")
