"""One-off check: can the cloud reach the new data sources, and what do the matching Kalshi markets look like?"""
import json
import re
import urllib.request

from .exchanges.kalshi import KalshiData

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept": "text/html,application/json;q=0.9,*/*;q=0.8", "Accept-Language": "en-GB,en;q=0.9"}
SOURCES = {
    "espn_nfl_browser": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    "espn_web": "https://site.web.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard",
    "sportsdb_day": "https://www.thesportsdb.com/api/v1/json/3/eventsday.php?d=2026-10-15&s=Soccer",
    "sportsdb_past_epl": "https://www.thesportsdb.com/api/v1/json/3/eventspastleague.php?id=4328",
    "tsa_browser": "https://www.tsa.gov/travel/security-screening/throughput",
    "clubelo_day": "http://api.clubelo.com/2026-10-15",
    "poly_epl": "https://gamma-api.polymarket.com/events?closed=false&limit=2&tag_slug=epl",
    "poly_nfl_games": "https://gamma-api.polymarket.com/events?closed=false&limit=2&tag_slug=nfl&order=startDate&ascending=true",
}
KEYWORDS = ["gas", "tsa", "rotten", "tennis", "atp", "wta", "passenger", "check"]


def fetch(url, n=1500):
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
    import re as _re
    for name, url in {"aaa_home": "https://gasprices.aaa.com/", "aaa_ca": "https://gasprices.aaa.com/?state=CA",
                      "tennis": "https://tennisabstract.com/reports/atp_elo_ratings.html",
                      "rt": "https://www.rottentomatoes.com/m/oppenheimer"}.items():
        try:
            req = urllib.request.Request(url, headers=UA)
            body = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
            if name.startswith("aaa"):
                i = body.find("Current Avg")
                out.append(f"## {name} around 'Current Avg':\n```\n{body[i-200:i+1800]}\n```")
            elif name == "tennis":
                i = body.find("Sinner")
                out.append(f"## tennis around Sinner:\n```\n{body[i-800:i+800]}\n```")
            else:
                for pat in ["criticsScore", "tomatometer", "ratingValue"]:
                    i = body.find(pat)
                    if i >= 0:
                        out.append(f"## rt '{pat}':\n```\n{body[i-200:i+300]}\n```")
                        break
        except Exception as e:
            out.append(f"## {name} failed {e}")
    if True:
        open("probe.md", "w").write("\n".join(out))
        return
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
