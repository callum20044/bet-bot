"""Live Betfair Exchange prices for the gap bot (read-only: never places a bet).

Logs in with BETFAIR_APP_KEY / BETFAIR_USERNAME / BETFAIR_PASSWORD (via the UK tunnel when it runs on GitHub)
and returns Match Odds lay prices AND how much money is waiting at that price, for the chosen competitions.
Never prints secrets.
"""
import difflib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CallumBetBot/1.0"
API = "https://api.betfair.com/exchange/betting/rest/v1.0/"
# Odds API sport key -> words that must all appear in Betfair's competition name
COMPS = {"soccer_epl": ["english", "premier league"], "soccer_efl_champ": ["english", "championship"]}
ALIAS = {"man utd": "manchester united", "man united": "manchester united", "man city": "manchester city",
         "spurs": "tottenham hotspur", "tottenham": "tottenham hotspur", "wolves": "wolverhampton wanderers",
         "nottm forest": "nottingham forest", "nott'm forest": "nottingham forest", "notts forest": "nottingham forest",
         "sheff utd": "sheffield united", "sheff wed": "sheffield wednesday", "qpr": "queens park rangers",
         "west brom": "west bromwich albion", "newcastle": "newcastle united", "brighton": "brighton and hove albion",
         "west ham": "west ham united", "leeds": "leeds united", "leicester": "leicester city", "ipswich": "ipswich town",
         "norwich": "norwich city", "stoke": "stoke city", "hull": "hull city", "cardiff": "cardiff city",
         "swansea": "swansea city", "coventry": "coventry city", "derby": "derby county", "luton": "luton town",
         "oxford utd": "oxford united", "plymouth": "plymouth argyle", "preston": "preston north end",
         "blackburn": "blackburn rovers", "bristol c": "bristol city", "birmingham": "birmingham city",
         "wrexham": "wrexham afc", "bolton": "bolton wanderers", "charlton": "charlton athletic"}


def _req(url, data, headers):
    r = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **headers})
    with urllib.request.urlopen(r, timeout=30) as f:
        return json.loads(f.read().decode())


def norm(name):
    n = name.lower().replace("&", "and").replace(".", "")
    n = re.sub(r"\b(fc|afc)\b", "", n).strip()
    n = re.sub(r"\s+", " ", n)
    n = ALIAS.get(n, n)
    n = re.sub(r"\butd\b", "united", re.sub(r"\bweds?\b", "wednesday", n))
    return re.sub(r"\s+", " ", n)


def same_team(a, b):
    a, b = norm(a), norm(b)
    return a == b or a.startswith(b) or b.startswith(a) or difflib.SequenceMatcher(None, a, b).ratio() >= 0.9


class Betfair:
    def __init__(self):
        self.key = os.environ.get("BETFAIR_APP_KEY", "").strip()
        user, pw = os.environ.get("BETFAIR_USERNAME", "").strip(), os.environ.get("BETFAIR_PASSWORD", "")
        if not (self.key and user and pw):
            raise RuntimeError("Betfair secrets not set")
        r = _req("https://identitysso.betfair.com/api/login", urllib.parse.urlencode({"username": user, "password": pw}).encode(),
                 {"X-Application": self.key, "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"})
        if r.get("status") != "SUCCESS":
            raise RuntimeError(f"Betfair login {r.get('status')} {r.get('error')}")
        self.h = {"X-Application": self.key, "X-Authentication": r["token"], "Content-Type": "application/json", "Accept": "application/json"}

    def call(self, op, body):
        return _req(API + op + "/", json.dumps(body).encode(), self.h)

    def matches(self, sport, days=8):
        """List of dict(home, away, kickoff, lay{outcome: (price, £ available)}) for one competition."""
        words = COMPS[sport]
        comps = self.call("listCompetitions", {"filter": {"eventTypeIds": ["1"]}})
        ids = [c["competition"]["id"] for c in comps if all(w in c["competition"]["name"].lower() for w in words)]
        if not ids:
            return []
        now = datetime.now(timezone.utc)
        cat = self.call("listMarketCatalogue", {"filter": {"competitionIds": ids, "marketTypeCodes": ["MATCH_ODDS"],
                        "marketStartTime": {"from": now.isoformat(), "to": (now + timedelta(days=days)).isoformat()}},
                        "maxResults": "100", "marketProjection": ["EVENT", "RUNNER_DESCRIPTION", "MARKET_START_TIME"]})
        out = []
        for i in range(0, len(cat), 40):
            part = cat[i:i + 40]
            books = {b["marketId"]: b for b in self.call("listMarketBook", {"marketIds": [m["marketId"] for m in part],
                     "priceProjection": {"priceData": ["EX_BEST_OFFERS"]}})}
            for m in part:
                b = books.get(m["marketId"])
                if not b or b.get("status") != "OPEN" or b.get("inplay"):
                    continue
                runners = sorted(m["runners"], key=lambda r: r.get("sortPriority", 9))
                if len(runners) != 3:
                    continue
                name = {r["selectionId"]: r["runnerName"] for r in runners}
                lay = {}
                for r in b["runners"]:
                    best = (r.get("ex", {}).get("availableToLay") or [None])[0]
                    if best:
                        lay[name.get(r["selectionId"], "?")] = (float(best["price"]), float(best["size"]))
                out.append(dict(home=runners[0]["runnerName"], away=runners[1]["runnerName"], draw=runners[2]["runnerName"],
                                kickoff=datetime.fromisoformat(m["marketStartTime"].replace("Z", "+00:00")), lay=lay,
                                matched=b.get("totalMatched", 0)))
        return out


def pair(game, bf_list):
    """Find the Betfair match for an Odds API game; return {Odds API outcome name: (lay price, £ available)} or None."""
    kick = datetime.fromisoformat(game["commence_time"].replace("Z", "+00:00"))
    for m in bf_list:
        if abs((m["kickoff"] - kick).total_seconds()) <= 3600 and same_team(m["home"], game["home_team"]) \
                and same_team(m["away"], game["away_team"]):
            lay = {}
            for ours, theirs in ((game["home_team"], m["home"]), (game["away_team"], m["away"]), ("Draw", m["draw"])):
                if theirs in m["lay"]:
                    lay[ours] = m["lay"][theirs]
            return lay
    return None
