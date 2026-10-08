"""Paper broker: pretends to buy contracts at the current ask, pays fees, and settles
positions when the market resolves. Everything is stored in SQLite so the dashboard
(and you) can inspect it, and the bot can be stopped and restarted without losing state."""
import sqlite3
from datetime import datetime, timedelta

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY,
    opened_at TEXT, ticker TEXT, event_ticker TEXT, title TEXT, side TEXT,
    contracts INTEGER, price REAL, fee REAL, cost REAL,
    strategy TEXT, est_prob REAL, edge REAL, reason TEXT, close_time TEXT,
    status TEXT DEFAULT 'open',          -- open | won | lost | void
    payout REAL DEFAULT 0, settled_at TEXT, mark REAL
);
CREATE TABLE IF NOT EXISTS equity (ts TEXT, cash REAL, equity REAL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


SNAP_SCHEMA = """
CREATE TABLE IF NOT EXISTS {p}snapshots (ticker TEXT, ts TEXT, yes_mid REAL, volume_24h INTEGER);
CREATE INDEX IF NOT EXISTS {p}snap_idx ON snapshots (ticker, ts);
"""


def connect(path=config.DB_PATH, snap_path=None):
    """Open the trade database. Recent price history (only needed for momentum) can live in a
    separate file via `snap_path`, which keeps the main database small - used in the cloud setup."""
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    if snap_path:
        db.execute("ATTACH DATABASE ? AS snap", (snap_path,))
        db.executescript(SNAP_SCHEMA.format(p="snap."))
    else:
        db.executescript(SNAP_SCHEMA.format(p=""))
    return db


class PaperBroker:
    def __init__(self, db, source, start=config.STARTING_BANKROLL):
        self.db, self.source = db, source
        row = db.execute("SELECT v FROM meta WHERE k='start'").fetchone()
        if row is None:
            db.execute("INSERT INTO meta VALUES ('start', ?)", (str(start),))
            db.execute("INSERT INTO meta VALUES ('source', ?)", (source.name,))
            db.commit()
            self.start = start
        else:
            self.start = float(row["v"])

    # ---- accounting ----
    def cash(self):
        spent = self.db.execute("SELECT COALESCE(SUM(cost + fee), 0) FROM trades").fetchone()[0]
        paid = self.db.execute("SELECT COALESCE(SUM(payout), 0) FROM trades").fetchone()[0]
        return self.start - spent + paid

    def open_trades(self):
        return self.db.execute("SELECT * FROM trades WHERE status='open'").fetchall()

    def open_value(self):
        # Mark open positions at the current BID (what you could actually sell for), not the mid.
        return sum((t["mark"] if t["mark"] is not None else t["price"]) * t["contracts"] for t in self.open_trades())

    def equity(self):
        return self.cash() + self.open_value()

    def realized_today(self, now):
        day = now.date().isoformat()
        r = self.db.execute(
            "SELECT COALESCE(SUM(payout - cost - fee), 0) FROM trades WHERE status!='open' AND substr(settled_at,1,10)=?",
            (day,)).fetchone()[0]
        return r

    # ---- trading ----
    def buy(self, sig, contracts):
        m, price = sig.market, sig.price
        fee = self.source.fee(contracts, price)
        cost = round(price * contracts, 4)
        if cost + fee > self.cash():
            return None
        cur = self.db.execute(
            """INSERT INTO trades (opened_at, ticker, event_ticker, title, side, contracts, price, fee, cost,
               strategy, est_prob, edge, reason, close_time, mark) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (self.source.now().isoformat(), m.ticker, m.event_ticker, m.title, sig.side, contracts, price, fee, cost,
             sig.strategy, sig.est_prob, sig.edge, sig.reason,
             m.resolve_time.isoformat() if m.resolve_time else None, m.bid(sig.side)))
        self.db.commit()
        return cur.lastrowid

    def update_marks(self, markets_by_ticker):
        for t in self.open_trades():
            m = markets_by_ticker.get(t["ticker"])
            if m is not None and m.bid(t["side"]) is not None:
                self.db.execute("UPDATE trades SET mark=? WHERE id=?", (m.bid(t["side"]), t["id"]))
        self.db.commit()

    def settle(self, open_tickers):
        """Check every open position whose market is no longer listed as open."""
        settled = []
        for t in self.open_trades():
            if t["ticker"] in open_tickers:
                continue
            try:
                m = self.source.get_market(t["ticker"])
            except Exception as e:  # network blip: try again next loop
                print(f"  ! couldn't check {t['ticker']}: {e}")
                continue
            now = self.source.now().isoformat()
            if m.result in ("yes", "no"):
                won = m.result == t["side"]
                payout = float(t["contracts"]) if won else 0.0
                status = "won" if won else "lost"
            elif m.status in ("settled", "finalized") and not m.result:
                payout, status = t["cost"] + t["fee"], "void"     # cancelled market: refund
            else:
                continue   # closed but not resolved yet
            self.db.execute("UPDATE trades SET status=?, payout=?, settled_at=?, mark=NULL WHERE id=?",
                            (status, payout, now, t["id"]))
            settled.append((t, status, payout))
        self.db.commit()
        return settled

    # ---- history ----
    def record_snapshots(self, markets, now):
        rows = [(m.ticker, now.isoformat(), m.yes_mid, m.volume_24h) for m in markets if m.yes_mid is not None]
        self.db.executemany("INSERT INTO snapshots VALUES (?,?,?,?)", rows)
        self.db.execute("DELETE FROM snapshots WHERE ts < ?", ((now - timedelta(hours=config.SNAPSHOT_KEEP_HOURS)).isoformat(),))
        self.db.commit()

    def price_ago(self, ticker, now, minutes):
        """Mid price closest to `minutes` ago (within a +/-50% window), or None."""
        target = now - timedelta(minutes=minutes)
        lo = (now - timedelta(minutes=minutes * 1.5)).isoformat()
        hi = (now - timedelta(minutes=minutes * 0.5)).isoformat()
        row = self.db.execute(
            "SELECT yes_mid, ts FROM snapshots WHERE ticker=? AND ts BETWEEN ? AND ? ORDER BY ts", (ticker, lo, hi)
        ).fetchall()
        if not row:
            return None
        best = min(row, key=lambda r: abs(datetime.fromisoformat(r["ts"]) - target))
        return best["yes_mid"]

    def record_equity(self, now):
        self.db.execute("INSERT INTO equity VALUES (?,?,?)", (now.isoformat(), self.cash(), self.equity()))
        self.db.commit()
