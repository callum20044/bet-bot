"""All the knobs in one place. Edit these, then restart the bot."""

# --- Account (paper money) ---
STARTING_BANKROLL = 1000.00     # pretend dollars you start with
EXTRA_PAPER_FUNDS = 1000.00     # top-ups added later (13 Oct: +$1,000 to speed up results)
DB_PATH = "predbot.db"          # where every trade and price snapshot is stored
SNAPSHOT_KEEP_HOURS = 6         # price history kept for the momentum strategy

# --- Loop ---
LOOP_SECONDS = 60               # how often to scan markets (live mode)
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"   # public, no login needed for prices
MAX_MARKET_PAGES = 40           # 1 page = up to 1000 markets
SCAN_DAYS = 6                   # scan markets closing in the next N days, soonest first

# --- Risk limits (applied to every trade, whatever the strategy says) ---
STAKE_PER_TRADE = 5.00          # dollars risked per bet (small, so lots of bets fit in the $1,000)
MAX_CONTRACTS = 200             # cap on contracts per bet
MAX_OPEN_POSITIONS = 400        # lots of bets at once = faster results
MAX_OPEN_PER_STRATEGY = 60     # each strategy gets its own share of room
MAX_PER_EVENT = 1               # each strategy bets at most once per event
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

# 3. Favourites near the finish: the flip side of the longshot fade. If people overpay for
#    unlikely outcomes, near-certain ones are slightly too cheap. Buy YES on outcomes priced
#    85-97c that resolve within a day. Small profit per bet, occasional full loss.
FAVOURITE = dict(
    enabled=True,
    yes_min=0.85, yes_max=0.97,
    max_hours_to_close=24,
    shrink=0.75,                    # same assumption as longshot: true NO chance = price x 0.75
    min_edge=0.005,                 # edges are thin at these prices, so accept 0.5c+
)

# 4. Whale follower: copy big bets. Each scan reads Kalshi's latest trades and follows anyone
#    who just put serious money on one side, unless the price has already moved away.
WHALE = dict(
    enabled=True,
    pages=5,                        # 5 x 1000 most recent trades (~30 seconds of Kalshi)
    min_trade_usd=500,              # ignore small trades when adding up a whale's bets
    min_usd=2000,                   # total on one side of one market to count as a whale
    price_min=0.10, price_max=0.90, # skip whales on near-certainties / lottery tickets
    max_chase=0.02,                 # don't pay more than 2c above what the whale paid
    nudge=0.03,                     # assumption: whales are right ~3c more often than price says
    max_hours_to_close=168,         # only markets finishing within a week
    max_lookups=25,                 # extra market lookups per scan
    min_edge=0.005,
)

# 5. Draw backer: buy YES on 'Tie' (football draws, hockey ties in regulation) priced 18-40c.
#    Assumption: draws are ~12% more likely than the price says, because casual bettors back teams.
DRAW = dict(
    enabled=True,
    yes_min=0.18, yes_max=0.40,
    max_hours_to_close=48,
    uplift=1.12,
    min_edge=0.0,                   # bet every draw in range - the scoreboard tests the idea
)

# 6. Broken-maths hunter: price groups that can't all be right (see arb.py). Near risk-free.
ARB = dict(
    enabled=True,
    stake=20.00,                    # dollars per basket (all legs together)
    min_profit_per_set=0.01,        # at least 1c locked-in profit per set after fees
)

# 7. Crypto clock: hourly BTC/ETH markets vs the live Coinbase price and recent volatility.
CRYPTO = dict(
    enabled=True,
    min_minutes=5, max_minutes=180, # markets finishing in 5 min to 3 hours
    vol_minutes=120,                # measure volatility over the last 2 hours
    vol_scale=1.0,                  # >1 if the model turns out over-confident
    price_min=0.05, price_max=0.95,
    min_edge=0.05,                  # big margin: weather taught us these markets can be sharp
)

# 8. Inflation nowcast: Kalshi CPI markets vs the Cleveland Fed's daily estimate.
NOWCAST = dict(
    enabled=True,
    max_hours_to_release=48,
    sd={"month": 0.10, "year": 0.10},   # typical miss of the nowcast (percentage points)
    price_min=0.05, price_max=0.95,
    min_edge=0.05,
)

# 9. Sharp bookie: Kalshi game prices vs Pinnacle (needs a free key from the-odds-api.com,
#    saved as the GitHub secret ODDS_API_KEY - sleeps until then).
SHARP = dict(
    enabled=True,
    sports=["soccer_epl", "soccer_uefa_champs_league", "americanfootball_nfl", "basketball_nba"],
    refresh_hours=6,                # 4 sports x 4 refreshes a day = ~480 of the 500 free calls/month
    price_min=0.05, price_max=0.95,
    min_edge=0.03,
)

# 10. Weather: Kalshi daily high-temperature markets vs the official US forecast (weather.gov).
#    The forecast high is treated as the centre of a bell curve; sigma is how far off forecasts
#    typically are (°F). Same-day bets also use what the station has already recorded.
WEATHER = dict(
    enabled=False,                  # switched off 13 Oct: lost 4-19 - the market knows the weather better
    max_days_ahead=1,               # today and tomorrow only
    sigma_today=1.2,                # baseline error of a same-day forecast high (°F)...
    sigma_tomorrow=1.6,             # ...and next-day
    sigma_later=2.5,
    spread_weight=1.0,              # ...plus however much the 7 forecast models disagree
    default_spread=1.5,             # used if the model data can't be fetched
    sigma_done=0.8,                 # late afternoon: the high has basically happened
    day_done_hour=17,               # local hour after which we trust today's recorded high
    obs_slack_f=1.0,                # recorded readings are rounded, so allow 1°F of slack
    bias_f=0.0,                     # shift if forecasts turn out to run hot/cold
    price_min=0.03, price_max=0.97,
    min_edge=0.05,                  # need 5c expected profit per contract - the model is simple
)
