"""Main loop: scan markets -> settle finished bets -> look for signals -> apply risk limits -> paper-buy."""
import argparse
import contextlib
import io
import sys
import threading
import time

from . import config, risk, strategies
from .broker import PaperBroker, connect
from .models import Signal


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)

    def flush(self):
        for st in self.streams:
            st.flush()


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
    weather_markets = []
    if config.WEATHER["enabled"] and hasattr(source, "list_series_markets"):
        from . import weather
        try:
            weather_markets = weather.fetch_markets(source)
        except Exception as e:
            print(f"  ! weather markets: {e}")
    by_ticker = {m.ticker: m for m in markets}
    for m in weather_markets:
        by_ticker[m.ticker] = m

    for t, status, payout in broker.settle(set(by_ticker)):
        pnl = payout - t["cost"] - t["fee"]
        print(f"  {'WIN ' if status == 'won' else status.upper():5} {pnl:+8.2f}  {t['strategy']:14} {t['side'].upper()} {t['title'][:60]}")

    weather_tickers = {m.ticker for m in weather_markets}
    tradable = [m for m in markets if risk.market_ok(m) and m.ticker not in weather_tickers]
    broker.record_snapshots(tradable, now)
    broker.update_marks(by_ticker)

    from . import extra, extra2
    signals = []
    for m in tradable:
        for strat in strategies.ALL + extra.PER_MARKET + extra2.PER_MARKET:
            s = strat(m, now, broker)
            if s:
                signals.append(s)
    if weather_markets:
        from . import weather
        signals += weather.signals([m for m in weather_markets if risk.market_ok(m)], now, broker)
    if config.CRYPTO["enabled"]:
        from . import crypto
        signals += crypto.signals(tradable, now, broker)
    if config.NOWCAST["enabled"]:
        from . import nowcast
        signals += nowcast.signals(source, now, broker, ok=risk.market_ok)
    if config.SHARP["enabled"]:
        from . import sharp
        signals += sharp.signals(tradable, now, broker)
        signals += sharp.extra_signals(tradable, markets, now, broker)
    if config.OVERROUND["enabled"]:
        from . import overround
        signals += overround.signals(source, markets, now, broker, ok=risk.market_ok)
    signals += extra.group_signals(tradable, markets, now, source, broker, risk.market_ok)
    signals += extra2.group_signals(tradable, markets, now)
    if config.WHALE["enabled"]:
        from . import whales
        signals += whales.signals(source, by_ticker, now, broker, ok=risk.market_ok)
    signals.sort(key=lambda s: s.edge, reverse=True)

    open_rows = broker.open_trades()
    open_tickers = {r["ticker"] for r in open_rows}
    open_events, by_strat = {}, {}
    for r in open_rows:
        ek = (r["strategy"], r["event_ticker"])
        open_events[ek] = open_events.get(ek, 0) + 1
        by_strat[r["strategy"]] = by_strat.get(r["strategy"], 0) + 1

    placed = 0
    if config.ARB["enabled"]:
        from . import arb
        for b in arb.find(tradable, markets):
            if any(m.ticker in open_tickers for m, _, _ in b.legs):
                continue
            if len(open_tickers) + len(b.legs) > config.MAX_OPEN_POSITIONS:
                break
            n, ok_all = b.n, True
            if sum(n * p + source.fee(n, p) for _, _, p in b.legs) > broker.cash():
                continue
            per_leg_edge = b.profit(n) / n / len(b.legs)
            for m, side, p in b.legs:
                sig = Signal(m, side, p, p + per_leg_edge, per_leg_edge, "arb", b.note)
                if broker.buy(sig, n):
                    open_tickers.add(m.ticker)
                    open_events[("arb", m.event_ticker)] = open_events.get(("arb", m.event_ticker), 0) + 1
                    by_strat["arb"] = by_strat.get("arb", 0) + 1
            placed += len(b.legs)
            print(f"  ARB  {n:4d} sets, locked profit ~${b.profit(n):.2f}  [{b.kind}] {b.legs[0][0].title[:50]}  ({b.note})")
    for s in signals:
        n, why = risk.size_and_check(s, broker, now, open_tickers, open_events, len(open_tickers), by_strat)
        if not n:
            if why in ("max open positions", "daily loss limit hit"):
                break
            continue
        if broker.buy(s, n):
            placed += 1
            open_tickers.add(s.market.ticker)
            ek = (s.strategy, s.market.event_ticker)
            open_events[ek] = open_events.get(ek, 0) + 1
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
    # one-off clean-up (13 Oct): the first maths-hunter run mistook unrelated rain markets for
    # price ladders. Those weren't real arbitrage, so remove them from the record.
    if not db.execute("SELECT 1 FROM meta WHERE k='fix_arb_sets'").fetchone():
        # second clean-up: 'complete sets' that were missing a quiet Tie market
        n = db.execute("DELETE FROM trades WHERE strategy='arb' AND reason LIKE 'all %'").rowcount
        db.execute("INSERT INTO meta VALUES ('fix_arb_sets', ?)", (str(n),))
        db.commit()
        if n:
            print(f"  (removed {n} bogus 'complete set' bets)")
    if not db.execute("SELECT 1 FROM meta WHERE k='fix_rain_arb'").fetchone():
        n = db.execute("DELETE FROM trades WHERE strategy='arb' AND ticker LIKE 'KXRAIN-%'").rowcount
        db.execute("INSERT INTO meta VALUES ('fix_rain_arb', ?)", (str(n),))
        db.commit()
        if n:
            print(f"  (removed {n} bogus rain 'arbitrage' bets)")
    if not db.execute("SELECT 1 FROM meta WHERE k='fix_sharp_match'").fetchone():
        n = db.execute("DELETE FROM trades WHERE strategy='sharp' AND opened_at < '2026-10-15T10:40'").rowcount
        db.execute("INSERT INTO meta VALUES ('fix_sharp_match', ?)", (str(n),))
        db.commit()
        if n:
            print(f"  (removed {n} mis-matched Pinnacle bets)")
    if not db.execute("SELECT 1 FROM meta WHERE k='fix_new6'").fetchone():
        n = db.execute("DELETE FROM trades WHERE strategy IN ('draw_model','ladder_smooth','field_power') "
                       "AND opened_at < '2026-10-15T17:45'").rowcount
        db.execute("INSERT INTO meta VALUES ('fix_new6', ?)", (str(n),))
        db.commit()
        if n:
            print(f"  (removed {n} bets from the first, faulty run of three new bots)")
    if not db.execute("SELECT 1 FROM meta WHERE k='fix_inplay'").fetchone():
        n = db.execute("DELETE FROM trades WHERE strategy='goals_poisson' AND opened_at < '2026-10-15T18:10'").rowcount
        db.execute("INSERT INTO meta VALUES ('fix_inplay', ?)", (str(n),))
        db.commit()
        if n:
            print(f"  (removed {n} goals bets placed on games already in play)")
    print(f"Paper trading on {source.name} | db {db_path} | start ${broker.start:,.2f} | Ctrl+C to stop")

    if args.dashboard:
        from .dashboard import serve
        threading.Thread(target=serve, args=(db_path,), daemon=True).start()

    i = 0
    try:
        while True:
            i += 1
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(Tee(sys.stdout, buf)):
                    step(source, broker)
            except Exception as e:      # keep running through network errors etc.
                print(f"  ! scan failed: {e}")
                buf.write(f"  ! scan failed: {e}\n")
            try:   # keep the last scan's log in the database so it can be read without GitHub access
                db.execute("INSERT OR REPLACE INTO meta VALUES ('last_scan_log', ?)", (buf.getvalue()[-20000:],))
                db.commit()
            except Exception:
                pass
            if args.loops and i >= args.loops:
                break
            if args.source != "sim":
                time.sleep(config.LOOP_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
