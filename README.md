# predbot – a paper-trading prediction-market bot

A no-frills version of the bot from the reel: it scans live **Kalshi** markets, decides what to bet
using simple rules, "buys" with **pretend money** at real prices (including Kalshi's real fees), settles
bets when the markets resolve, and shows the results on a local dashboard.

No account, no API keys, no real money. Kalshi's price data is public.

## Run it

**Windows:** double-click `start.bat`. It opens the dashboard in your browser after a few seconds. Keep the black window open; closing it stops the bot.

Other systems: you need Python 3.9+ (no packages to install).

```bash
cd predbot
python3 -m predbot.bot --dashboard
```

Then open **http://localhost:8050** in your browser. It scans every 60 seconds; leave it running
(Ctrl+C stops it, and it picks up where it left off next time - everything is saved in `predbot.db`).

Test without internet using the simulator:

```bash
python3 -m predbot.bot --source sim --loops 500 --dashboard
```

To start over, delete `predbot.db`.

## What it does each scan

1. Downloads open Kalshi markets (skips the auto-generated parlay ones).
2. Settles any of your bets whose market has resolved: $1 per contract if you were right, $0 if not.
3. Throws away markets that are illiquid or have a wide bid/ask gap.
4. Runs each strategy; each one estimates a "true" probability and the expected profit per contract after fees.
5. Applies risk limits (stake size, max 25 open bets, max 15 per strategy, one bet per event, daily loss stop).
6. Paper-buys at the **ask** price (what you'd really pay) and values open bets at the **bid** (what you could really sell for).

## The strategies (edit in `predbot/config.py`)

- **longshot_fade** – Research on Kalshi trading data finds cheap YES contracts (5–15c "longshots") win
  less often than their price suggests. So it buys NO on them close to resolution. Lots of small wins,
  occasional ~$10 loss. The key setting is `shrink`: how overpriced you think longshots are.
- **momentum** – If a price jumped 8c+ in 30 minutes, it bets news is still being priced in. Experimental.

## Reading the results honestly

- **Win rate means little on its own.** Longshot fade can win 90% of bets and still lose money, since one loss wipes out ~8 wins.
  Look at **Actual P&L vs Expected** on the strategy scoreboard.
- Give it **at least a few hundred settled bets** before believing anything. Many markets take days to resolve.
- Paper fills are optimistic: real orders can move the price, and the ask might only have a few contracts behind it.
- If a strategy loses on paper, it'll lose for real. Turn it off (`enabled=False`) or change its settings.

## Going real later (Betfair)

Kalshi is US-only for now, so real-money trading from the UK means Betfair Exchange. `predbot/exchanges/betfair.py`
sets out what's needed. The bot is built so a new data source/broker can be plugged in without touching the
strategies or risk limits. Only do that once paper results are positive over a decent sample, and start with tiny stakes.

## Files

```
predbot/config.py        all settings
predbot/bot.py           main loop
predbot/strategies.py    the betting rules
predbot/risk.py          hard limits
predbot/broker.py        paper account + SQLite storage
predbot/fees.py          Kalshi fee formula
predbot/dashboard.py     local web dashboard
predbot/exchanges/       kalshi (live), simulated (offline test), betfair (stub)
```

## Running it on GitHub (free, 24/7)

`.github/workflows/scan.yml` makes GitHub run one scan about every 10 minutes:

- trades are saved to `predbot.db` on a branch called `data` (only the latest copy is kept);
- recent price history (for momentum) lives in GitHub's Actions cache;
- the dashboard is published with GitHub Pages at `https://<your-username>.github.io/<repo-name>/`.

Setup: upload these files to a new public repo, then Settings -> Pages -> Source: **GitHub Actions**,
then Actions -> scan -> **Run workflow**. To start over, delete the `data` branch.
GitHub pauses scheduled runs if a repo looks inactive for 60 days; if scans stop, re-enable the workflow in the Actions tab.
