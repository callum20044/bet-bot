"""Main loop: scan markets -> settle finished bets -> look for signals -> apply risk limits -> paper-buy."""
import argparse
import threading
import time

from . import config, risk, strategies
from .broker import PaperBroker, connect


def make_source(name):
    if name == "kalshi":
        from .exchanges.kalshi import KalshiData
        return KalshiData()
    if name == "sim":
        from .exchanges.simulated import SimulatedData
        return SimulatedData()
    if name == "betfair":
        from .exchanges.betfair import BetfairData
        return BetfairData()
    raise SystemExit(f"unknown source {name}")


def step(source, broker, verbose=True):
    now = source.now()
    markets = source.list_open_markets()
    by_ticker = {m.ticker: m for m in markets}

    for t, status, payout in broker.settle(set(by_ticker)):
        pnl = payout - t["cost"] - t["fee"]
        print(f"  {'WIN ' if status == 'won' else status.upper():5} {pnl:+8.2f}  {t['strategy']:14} {t['side'].upper()} {t['title'][:60]}")

    tradable = [m for m in markets if risk.market_ok(m)]
    broker.record_snapshots(tradable, now)
    broker.update_marks(by_ticker)

    signals = []
    for m in tradable:
        for strat in strategies.ALL:
            s = strat(m, now, broker)
            if s:
                signals.append(s)
    signals.sort(key=lambda s: s.edge, reverse=True)

    open_rows = broker.open_trades()
    open_tickers = {r["ticker"] for r in open_rows}
    open_events, by_strat = {}, {}
    for r in open_rows:
        open_events[r["event_ticker"]] = open_events.get(r["event_ticker"], 0) + 1
        by_strat[r["strategy"]] = by_strat.get(r["strategy"], 0) + 1

    placed = 0
    for s in signals:
        n, why = risk.size_and_check(s, broker, now, open_tickers, open_events, len(open_tickers), by_strat)
        if not n:
            if why in ("max open positions", "daily loss limit hit"):
                break
            continue
        if broker.buy(s, n):
            placed += 1
            open_tickers.add(s.market.ticker)
            open_events[s.market.event_ticker] = open_events.get(s.market.event_ticker, 0) + 1
            by_strat[s.strategy] = by_strat.get(s.strategy, 0) + 1
            print(f"  BUY  {n:4d} x {s.side.upper():3} @ {s.price*100:4.1f}c  edge {s.edge*100:4.1f}c  "
                  f"[{s.strategy}] {s.market.title[:50]}  ({s.reason})")

    broker.record_equity(now)
    if verbose:
        print(f"[{now:%Y-%m-%d %H:%M}] markets {len(markets)}, tradable {len(tradable)}, signals {len(signals)}, "
              f"new bets {placed}, open {len(open_tickers)} | cash ${broker.cash():,.2f}  equity ${broker.equity():,.2f}")


def main():
    ap = argparse.ArgumentParser(description="Paper-trading prediction-market bot")
    ap.add_argument("--source", default="kalshi", choices=["kalshi", "sim", "betfair"],
                    help="kalshi = live public Kalshi prices (default); sim = offline simulator for testing")
    ap.add_argument("--loops", type=int, default=0, help="stop after N scans (0 = run forever)")
    ap.add_argument("--db", default=None, help="database file (default from config)")
    ap.add_argument("--snapshots", default=None, help="separate file for price history (cloud setup)")
    ap.add_argument("--dashboard", action="store_true", help="also serve the dashboard on http://localhost:8050")
    args = ap.parse_args()

    db_path = args.db or (config.DB_PATH if args.source != "sim" else "predbot_sim.db")
    source = make_source(args.source)
    db = connect(db_path, args.snapshots)
    broker = PaperBroker(db, source)
    print(f"Paper trading on {source.name} | db {db_path} | start ${broker.start:,.2f} | Ctrl+C to stop")

    if args.dashboard:
        from .dashboard import serve
        threading.Thread(target=serve, args=(db_path,), daemon=True).start()

    i = 0
    try:
        while True:
            i += 1
            try:
                step(source, broker)
            except Exception as e:      # keep running through network errors etc.
                print(f"  ! scan failed: {e}")
            if args.loops and i >= args.loops:
                break
            if args.source != "sim":
                time.sleep(config.LOOP_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
