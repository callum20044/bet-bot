"""One-off check: can the cloud reach the new data sources, and what do the matching Kalshi markets look like?"""
import json
import re
import urllib.request

from .exchanges.kalshi import KalshiData

UA = {"User-Agent": "Mozilla/5.0 (predbot paper trading)", "Accept": "*/*"}
SOURCES = {
    "espn_nfl": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    "espn_epl": "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard",
    "espn_nhl": "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard",
    "clubelo_fixtures": "http://api.clubelo.com/Fixtures",
    "tennis_elo_atp": "https://tennisabstract.com/reports/atp_elo_ratings.html",
    "tennis_elo_wta": "https://tennisabstract.com/reports/wta_elo_ratings.html",
    "tsa": "https://www.tsa.gov/travel/security-screening/throughput",
    "aaa": "https://gasprices.aaa.com/",
    "eia_gas_csv": "https://www.eia.gov/dnav/pet/hist_xls/EMM_EPMR_PTE_NUS_DPGw.xls",
    "polymarket": "https://gamma-api.polymarket.com/events?closed=false&limit=3&tag_slug=nfl",
    "rt_example": "https://www.rottentomatoes.com/m/oppenheimer",
}
KEYWORDS = ["gas", "tsa", "rotten", "tennis", "atp", "wta", "passenger", "check"]


def fetch(url, n=700):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
    return r.status, len(body), body[:n].decode("utf-8", "replace")


def main():
    out = ["# Probe", ""]
    for name, url in SOURCES.items():
        try:
            st, size, head = fetch(url)
            out.append(f"## {name}: HTTP {st}, {size} bytes\n```\n{head}\n```")
        except Exception as e:
            out.append(f"## {name}: FAILED {e}")
    k = KalshiData()
    try:
        series = k._get("/series", {}).get("series", [])
    except Exception as e:
        series = []
        out.append(f"series list failed: {e}")
    out.append(f"\n# Kalshi series ({len(series)} total) matching keywords")
    hits = [s for s in series if any(w in (s.get("title", "") + " " + s.get("ticker", "")).lower() for w in KEYWORDS)]
    for s in hits[:80]:
        out.append(f"- {s.get('ticker')} | {s.get('title')} | {s.get('category')} | {s.get('frequency')}")
    shown = 0
    for s in hits:
        if shown >= 14:
            break
        try:
            ms = k._get("/markets", {"series_ticker": s["ticker"], "status": "open", "limit": 3}).get("markets", [])
        except Exception:
            continue
        if not ms:
            continue
        shown += 1
        out.append(f"\n### {s['ticker']} sample markets")
        for m in ms[:3]:
            out.append("```\n" + json.dumps({x: m.get(x) for x in ("ticker", "event_ticker", "title", "yes_sub_title", "strike_type",
                       "floor_strike", "cap_strike", "yes_bid_dollars", "yes_ask_dollars", "volume_24h_fp",
                       "expected_expiration_time", "close_time")}, indent=0) + "\n" + (m.get("rules_primary") or "")[:300] + "\n```")
    # finished-game sniping: game markets trading 90-99c
    try:
        ms = k.list_open_markets()
        hi = [m for m in ms if "GAME" in m.event_ticker.split("-")[0] and m.yes_bid and m.yes_bid >= 0.90 and m.yes_ask and m.yes_ask < 1]
        out.append(f"\n# GAME markets with YES bid >= 90c: {len(hi)}")
        for m in hi[:25]:
            out.append(f"- {m.ticker} bid {m.yes_bid} ask {m.yes_ask} vol {m.volume_24h:.0f} exp {m.expected_time} | {m.title}")
    except Exception as e:
        out.append(f"scan failed {e}")
    open("probe.md", "w").write("\n".join(out))
    print("\n".join(out)[:3000])


if __name__ == "__main__":
    main()
