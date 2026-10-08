"""Read-only Kalshi client using the public market-data API (no account or keys needed)."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .. import config
from ..fees import kalshi_taker_fee
from ..models import Market


def _parse_time(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _price(m, key):
    """Kalshi returns prices either as cents (yes_ask: 45) or dollar strings (yes_ask_dollars: "0.4500")."""
    v = m.get(key + "_dollars")
    if v not in (None, ""):
        p = float(v)
    elif m.get(key) is not None:
        p = m[key] / 100
    else:
        return None
    return p if 0 < p < 1 else None   # 0 or 100 means "no orders on that side"


def _num(m, *keys):
    for k in keys:
        v = m.get(k)
        if v not in (None, ""):
            try:
                return int(float(v))
            except ValueError:
                pass
    return 0


def to_market(m) -> Market:
    yes_bid, yes_ask = _price(m, "yes_bid"), _price(m, "yes_ask")
    no_bid, no_ask = _price(m, "no_bid"), _price(m, "no_ask")
    # Binary market: a YES bid is a NO ask and vice versa. Fill gaps if the API omits one side.
    if no_ask is None and yes_bid is not None:
        no_ask = round(1 - yes_bid, 4)
    if no_bid is None and yes_ask is not None:
        no_bid = round(1 - yes_ask, 4)
    return Market(
        ticker=m["ticker"],
        title=(m.get("title") or "") + (f" - {m['yes_sub_title']}" if m.get("yes_sub_title") else ""),
        event_ticker=m.get("event_ticker", ""),
        yes_bid=yes_bid, yes_ask=yes_ask, no_bid=no_bid, no_ask=no_ask,
        volume_24h=_num(m, "volume_24h_fp", "volume_24h"),
        close_time=_parse_time(m.get("close_time")),
        status=m.get("status", ""),
        result=(m.get("result") or "").lower(),
        expected_time=_parse_time(m.get("expected_expiration_time")),
    )


class KalshiData:
    name = "kalshi"

    def __init__(self, base=config.KALSHI_API):
        self.base = base.rstrip("/")
        self._mve_param = True
        self._close_param = True

    def now(self):
        return datetime.now(timezone.utc)

    def fee(self, contracts, price):
        return kalshi_taker_fee(contracts, price)

    def _get(self, path, params=None):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "predbot/0.1"})
        last = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:      # rate limited / server hiccup: back off
                    last = e
                    time.sleep(2 ** attempt)
                    continue
                raise
            except urllib.error.URLError as e:
                last = e
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Kalshi request failed: {url}: {last}")

    def list_open_markets(self):
        out, cursor = [], None
        for _ in range(config.MAX_MARKET_PAGES):
            params = {"status": "open", "limit": 1000}
            if self._mve_param:
                params["mve_filter"] = "exclude"     # skip auto-generated multi-leg parlay markets
            if self._close_param:
                # Only markets closing within the next week - the strategies never bet further out,
                # and Kalshi lists tens of thousands of long-dated markets that would eat the page limit.
                now = time.time()
                params["min_close_ts"] = int(now)
                params["max_close_ts"] = int(now + 10 * 24 * 3600)   # close_time runs ~3 days past the event
            if cursor:
                params["cursor"] = cursor
            try:
                data = self._get("/markets", params)
            except urllib.error.HTTPError as e:
                if e.code == 400 and self._close_param:
                    self._close_param = False
                    print("  (Kalshi rejected the close-time filter - scanning without it)")
                    continue
                if e.code == 400 and self._mve_param:
                    self._mve_param = False
                    continue
                raise
            for m in data.get("markets", []):
                if m.get("ticker", "").startswith("KXMVE"):
                    continue
                out.append(to_market(m))
            cursor = data.get("cursor")
            if not cursor:
                break
        return out

    def get_market(self, ticker):
        return to_market(self._get(f"/markets/{urllib.parse.quote(ticker)}")["market"])
