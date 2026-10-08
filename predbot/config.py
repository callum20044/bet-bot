"""All the knobs in one place. Edit these, then restart the bot."""

# --- Account (paper money) ---
STARTING_BANKROLL = 1000.00     # pretend dollars you start with
DB_PATH = "predbot.db"          # where every trade and price snapshot is stored
SNAPSHOT_KEEP_HOURS = 6         # price history kept for the momentum strategy

# --- Loop ---
LOOP_SECONDS = 60               # how often to scan markets (live mode)
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"   # public, no login needed for prices
MAX_MARKET_PAGES = 10           # 1 page = up to 1000 markets

# --- Risk limits (applied to every trade, whatever the strategy says) ---
STAKE_PER_TRADE = 10.00         # dollars risked per bet
MAX_CONTRACTS = 200             # cap on contracts per bet
MAX_OPEN_POSITIONS = 25         # same cap as the bot in the video
MAX_OPEN_PER_STRATEGY = 15     # so one strategy can't hog every slot
MAX_PER_EVENT = 1               # don't stack bets on the same event
DAILY_LOSS_LIMIT = 100.00       # stop opening new bets for the day after losing this much (realized)
MIN_VOLUME_24H = 200            # ignore markets nobody is trading
MAX_SPREAD = 0.05               # ignore markets where bid/ask gap is wider than 5c
MIN_PRICE, MAX_PRICE = 0.02, 0.98   # never buy contracts priced outside this range

# --- Strategies ---
# 1. Longshot fade: research on Kalshi data finds cheap "YES" contracts (longshots) win less often
#    than their price implies. So we buy NO on them, close to resolution.
#    `shrink` is the core assumption: true YES chance = market price x shrink.
#    0.75 means "a 10c longshot really only wins ~7.5% of the time". Paper trading tests this.
LONGSHOT = dict(
    enabled=True,
    yes_min=0.03, yes_max=0.15,     # only look at YES priced 3c-15c
    max_hours_to_close=72,          # only markets resolving within 3 days
    shrink=0.75,
    min_edge=0.01,                  # need at least 1c expected profit per contract after fees
)

# 2. Momentum: if a market's price jumped sharply recently, assume news is still being priced in
#    and ride it. Experimental - the per-strategy scoreboard will tell you if it earns its keep.
MOMENTUM = dict(
    enabled=True,
    lookback_minutes=30,
    min_move=0.08,                  # price must have moved at least 8c in the lookback window
    follow_through=0.35,            # assume a further 35% of the move is still to come
    price_min=0.15, price_max=0.85, # avoid the extremes
    max_hours_to_close=48,
    min_edge=0.02,
)
