"""All the knobs in one place. Edit these, then restart the bot."""

# --- Account (paper money) ---
STARTING_BANKROLL = 1000.00     # pretend dollars you start with
EXTRA_PAPER_FUNDS = 2000.00     # top-ups: +$1,000 on 13 Oct, +$1,000 on 15 Oct for the six new bots
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
MAX_OPEN_POSITIONS = 650        # lots of bets at once = faster results
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
    enabled=False,                  # switched off 14 Oct: won 67% but lost $56 - it bought after the market had already overshot
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
    enabled=False,                  # switched off 15 Oct: won 460 of 510 but -$12 after fees - wins too small to cover them
    yes_min=0.85, yes_max=0.97,
    max_hours_to_close=24,
    shrink=0.75,                    # same assumption as longshot: true NO chance = price x 0.75
    min_edge=0.005,                 # edges are thin at these prices, so accept 0.5c+
)

# 4. Whale follower: copy big bets. Each scan reads Kalshi's latest trades and follows anyone
#    who just put serious money on one side, unless the price has already moved away.
WHALE = dict(
    enabled=False,                  # switched off 14 Oct: lost $44 on 41 bets - big bettors weren't better informed
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

# 11. Spike fade: the mirror of momentum (which lost). After a sharp jump, bet it drifts back.
SPIKE_FADE = dict(
    enabled=False,                  # switched off 15 Oct: lost $69 on 236 bets overnight (92-175 overall)
    lookback_minutes=30,
    min_move=0.08,
    revert=0.35,                    # assume 35% of the jump gets given back
    price_min=0.15, price_max=0.85,
    max_hours_to_close=48,
    min_edge=0.02,
)

# 12. Underdog fade: like longshot fade but for YES priced 15-30c (a smaller assumed bias).
UNDERDOG = dict(
    enabled=True,
    yes_min=0.15, yes_max=0.30,
    max_hours_to_close=72,
    shrink=0.85,                    # true YES chance = price x 0.85
    min_edge=0.005,
)

# 13. Overround fade: 'who will win?' events (only one outcome can happen) whose YES prices add
#     up to well over 100%. Bet NO on mid-priced candidates, valued at price / total.
OVERROUND = dict(
    enabled=True,
    min_outcomes=3,
    min_overround=1.08,             # prices must add up to at least 108%...
    max_overround=1.80,             # ...but not absurdly more (probably not one-winner-only)
    yes_min=0.05, yes_max=0.40,
    max_hours_to_close=72,
    max_lookups=80,                 # Kalshi event lookups per scan (results are cached)
    min_edge=0.01,
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
    enabled=False,                  # switched off 14 Oct: lost $45 where it expected +$114 - the market is sharper than the model
    min_minutes=5, max_minutes=180, # markets finishing in 5 min to 3 hours
    vol_minutes=120,                # measure volatility over the last 2 hours
    vol_scale=1.0,                  # >1 if the model turns out over-confident
    price_min=0.05, price_max=0.95,
    min_edge=0.05,                  # big margin: weather taught us these markets can be sharp
)

# 8. Inflation nowcast: Kalshi CPI markets vs the Cleveland Fed's daily estimate.
NOWCAST = dict(
    enabled=False,                  # switched off 14 Oct: 0-4 on its first CPI release (hotter than the Fed's estimate)
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
    max_disagree=0.15,              # 19 Oct: bigger gaps were stale odds on games already in play
)

# 14. Pinnacle draws: Kalshi football 'Tie' markets vs Pinnacle's draw price (same odds download).
SHARP_DRAW = dict(
    enabled=True,
    price_min=0.10, price_max=0.60,
    min_edge=0.03,
    max_disagree=0.15,
)

# 15. Pinnacle spreads: 'X wins by over N points' (NFL/NBA), priced from Pinnacle's win odds with a
#     bell curve of final margins. The curve is rougher than the bookie's odds, so a bigger edge is needed.
SHARP_SPREAD = dict(
    enabled=True,
    sd={"KXNFLSPREAD": 13.5, "KXNBASPREAD": 12.0},   # typical spread of the final margin, points
    price_min=0.10, price_max=0.90,
    min_edge=0.05,
    max_disagree=0.15,
)

# ----- 16-21: six more, added 15 Oct (each capped at 25 open bets so they get a fair test) -----
# 16. Mid fade: NO on YES priced 30-45c - does the 'YES is overpriced' edge reach the middle?
MID_FADE = dict(enabled=False,  # off 17 Oct: 25-23, -$22 - the YES-overpricing edge doesn't reach 30-45c
                max_open=25, yes_min=0.30, yes_max=0.45, max_hours_to_close=72,
                shrink=0.92, min_edge=0.005)
# 17. Strong favourite: YES on 70-85c favourites resolving within a day.
STRONG_FAV = dict(enabled=False,  # off 17 Oct: 56-27, -$34 - favourites lose too often to pay
                  max_open=25, yes_min=0.70, yes_max=0.85, max_hours_to_close=24,
                  shrink=0.85, min_edge=0.005)
# 18. Ladder smoothing: an 'over X' rung priced well off the line between its neighbours.
LADDER = dict(enabled=True, max_open=25, min_rungs=4, min_gap=0.06, trust_rung=0.3,
              max_hours_to_close=72, min_edge=0.02)
# 19. Spread vs moneyline: 'X wins by over N' priced from Kalshi's own 'X wins' price.
SPREAD_ML = dict(enabled=False,  # off 18 Oct: 8-23, -$83 (t=-3.4) - kept backing big favourites to cover big college spreads
                 max_open=25, min_hours_to_close=3.5, max_hours_to_close=96, min_edge=0.04,
                 sd={"KXNFLSPREAD": 13.5, "KXNBASPREAD": 12.0, "KXNCAAFSPREAD": 16.0, "KXNCAAMBSPREAD": 11.0})
# 20. Field power: in one-winner fields, true chance ~ price^1.15 (favourites underbet).
FIELD = dict(enabled=True, max_open=25, min_outcomes=5, min_total=0.90, max_total=1.50, alpha=1.15,
             price_min=0.03, price_max=0.85, max_hours_to_close=72, max_lookups=40, min_edge=0.02)
# 21. Draw model: football draw chance from how even the two teams' prices are.
DRAW_MODEL = dict(enabled=True, max_open=25, base=0.29, slope=0.31, min_hours_to_close=4.5, max_hours_to_close=48, min_edge=0.03)

# ----- 22-26: five more, added 15 Oct evening (20 open bets each) -----
# 22. Half-time draws: ~42% of first halves end level (less when one side is much stronger).
HALF_DRAW = dict(enabled=False,  # off 18 Oct: 1-8, -$31 (t=-2.3)
                  max_open=20, base=0.45, slope=0.25, min_hours_full_time=4.5,
                 max_hours_to_close=48, min_edge=0.03)
# 23. Goals ladders: fit one Poisson curve to 'over 0.5 / 1.5 / 2.5... goals', bet the odd rung out.
GOALS = dict(enabled=True, max_open=20, min_rungs=3, min_gap=0.05, trust_rung=0.3,
             min_hours_to_close=4.5,             # Kalshi expects football results 3-4h after kick-off
             max_hours_to_close=48, min_edge=0.02)
# 24. NHL / MLB 'wins by over 1.5', from Kalshi's own win price with a Poisson scoring model.
LOWSCORE = dict(enabled=False,  # off 16 Oct: 1-6, -$30 - bet on big wins during games already in play
                max_open=20, total={"KXNHLSPREAD": 6.0, "KXMLBSPREAD": 8.6},
                min_hours_to_close=3.5, max_hours_to_close=72, min_edge=0.04)
# 25. Crypto with the options market's volatility (Deribit DVOL) - the old crypto bot used 2h wiggles.
CRYPTO_IV = dict(enabled=False,  # off 16 Oct: 18-37, -$25 - second crypto model to lose; Kalshi prices crypto well
                  max_open=20, min_minutes=30, max_minutes=24 * 60,
                 price_min=0.05, price_max=0.95, min_edge=0.04)
# 26. 'Will X say Y?' markets: bet NO on YES 10-60c.
MENTION = dict(enabled=True, max_open=20, yes_min=0.10, yes_max=0.60, max_hours_to_close=48,
               shrink=0.85, min_edge=0.01)

# ----- 27-32: outside-data bots, added 15 Oct late (20 open bets each) -----
# 27. Result sniper: ESPN says the game is over, Kalshi still trades below $1 - buy the result.
SNIPER = dict(enabled=True, max_open=20, certainty=0.995, price_min=0.50, price_max=0.98, min_edge=0.005)
# 28. Petrol prices: tomorrow's AAA average ~ today + recent trend.
GAS = dict(enabled=False,  # off 16 Oct: 2-18, -$76 - kept buying 5c longshots the market knew were dead
           max_open=20, base_sd=0.004, price_min=0.05, price_max=0.95, min_edge=0.04)
# 29. Football ratings (ClubElo) -> win / draw / loss chances.
FOOTBALL_ELO = dict(enabled=True, max_open=20, home_adv=60, draw_base=0.29, draw_slope=0.31,
                    min_hours_to_close=4.5, max_hours_to_close=72, max_disagree=0.20, min_edge=0.04)
# 30. Tennis ratings (Tennis Abstract Elo, blend of overall and hard court).
TENNIS_ELO = dict(enabled=True, max_open=20, min_hours_to_close=4, max_hours_to_close=48,
                  max_disagree=0.25, min_edge=0.05)
# 31. Polymarket vs Kalshi: same game, different price.
POLY = dict(enabled=True, max_open=20, min_hours_to_close=3.5, max_hours_to_close=96, max_disagree=0.15, min_edge=0.04)
# 32. Rotten Tomatoes score markets vs the film's current score (only once it has plenty of reviews).
ROTTEN = dict(enabled=True, max_open=20, min_reviews=60, max_series=25, min_edge=0.05)

# ----- 33-35: replacements, added 16 Oct - stretch the bots that are working -----
# 33/34. Underdog / longshot fades for markets settling 3-6 days out (originals stop at 3 days).
UNDERDOG_LONG = dict(enabled=True, max_open=25, yes_min=0.15, yes_max=0.30, min_hours_to_close=72,
                     max_hours_to_close=144, shrink=0.85, min_edge=0.005)
LONGSHOT_LONG = dict(enabled=True, max_open=25, yes_min=0.03, yes_max=0.15, min_hours_to_close=72,
                     max_hours_to_close=144, shrink=0.75, min_edge=0.01)
# 35. Ladder smoothing for 3-rung ladders (the middle rung vs its two neighbours).
LADDER3 = dict(enabled=False,  # off 18 Oct: 10-15, -$40 (t=-1.7) - 3 rungs is too few to judge the curve
                max_open=25, min_rungs=3, min_gap=0.06, trust_rung=0.3,
               max_hours_to_close=72, min_edge=0.02)

# ----- 36: added 19 Oct (weekly review) -----
# 36. Longshot fade without in-play sport. The data: longshot_fade on sport markets in their last 6 hours
#     (games already under way) went 173 bets, -$41; the same bets 6h+ out went 195 bets, +$65; non-sport
#     markets made money at every distance. So: NO on YES 3-15c, but sport only while 6h+ from the result.
LONGSHOT_PRE = dict(enabled=True, max_open=20, yes_min=0.03, yes_max=0.15, max_hours_to_close=72,
                    min_hours_sport=6, shrink=0.75, min_edge=0.01)

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


# Which settings block belongs to which strategy name (used e.g. by the daily loss limit, which
# only counts strategies that are still switched on).
STRATEGY_CONFIG = {
    "longshot_fade": "LONGSHOT", "momentum": "MOMENTUM", "favourite": "FAVOURITE", "whale": "WHALE",
    "draw": "DRAW", "arb": "ARB", "crypto": "CRYPTO", "nowcast": "NOWCAST", "sharp": "SHARP",
    "weather": "WEATHER", "spike_fade": "SPIKE_FADE", "underdog": "UNDERDOG", "overround": "OVERROUND",
    "sharp_draw": "SHARP_DRAW", "sharp_spread": "SHARP_SPREAD",
    "mid_fade": "MID_FADE", "strong_favourite": "STRONG_FAV", "ladder_smooth": "LADDER",
    "spread_moneyline": "SPREAD_ML", "field_power": "FIELD", "draw_model": "DRAW_MODEL",
    "half_draw": "HALF_DRAW", "goals_poisson": "GOALS", "lowscore_spread": "LOWSCORE",
    "crypto_iv": "CRYPTO_IV", "mention_fade": "MENTION",
    "result_sniper": "SNIPER", "gas_price": "GAS", "football_elo": "FOOTBALL_ELO", "tennis_elo": "TENNIS_ELO",
    "polymarket": "POLY", "rotten": "ROTTEN",
    "underdog_long": "UNDERDOG_LONG", "longshot_long": "LONGSHOT_LONG", "ladder3": "LADDER3",
    "longshot_prematch": "LONGSHOT_PRE",
}


def enabled_strategies():
    g = globals()
    return {name for name, block in STRATEGY_CONFIG.items() if g.get(block, {}).get("enabled")}
