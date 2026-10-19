"""One-off: can GitHub's machine log in to Betfair with the delayed key and read prices? Never prints secrets."""
import json, os, urllib.request, urllib.parse

KEY, USER, PW = (os.environ.get(k, "") for k in ("BETFAIR_APP_KEY", "BETFAIR_USERNAME", "BETFAIR_PASSWORD"))
print("secrets present:", {"key": bool(KEY), "user": bool(USER), "password": bool(PW)})

def req(url, data=None, headers=None):
    r = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(r, timeout=30) as f:
        return json.loads(f.read().decode())

try:
    ip = req("https://ipinfo.io/json")
    print("runner location:", ip.get("country"), ip.get("region"))
except Exception as e:
    print("location lookup failed:", e)

login = req("https://identitysso.betfair.com/api/login",
            urllib.parse.urlencode({"username": USER, "password": PW}).encode(),
            {"X-Application": KEY, "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"})
print("login status:", login.get("status"), login.get("error"))
tok = login.get("token")
if not tok:
    raise SystemExit("LOGIN FAILED")

H = {"X-Application": KEY, "X-Authentication": tok, "Content-Type": "application/json", "Accept": "application/json"}
B = "https://api.betfair.com/exchange/betting/rest/v1.0/"
try:
    types = req(B + "listEventTypes/", json.dumps({"filter": {}}).encode(), H)
    print("sports:", ", ".join(f"{t['eventType']['name']}({t['marketCount']})" for t in types[:12]))
    mk = req(B + "listMarketCatalogue/", json.dumps({"filter": {"eventTypeIds": ["1"], "marketTypeCodes": ["MATCH_ODDS"]},
             "maxResults": "3", "marketProjection": ["EVENT", "RUNNER_DESCRIPTION"]}).encode(), H)
    ids = [m["marketId"] for m in mk]
    books = req(B + "listMarketBook/", json.dumps({"marketIds": ids, "priceProjection": {"priceData": ["EX_BEST_OFFERS"]}}).encode(), H)
    for m, b in zip(mk, books):
        names = {r["selectionId"]: r["runnerName"] for r in m["runners"]}
        px = [f"{names.get(r['selectionId'])} back {((r['ex'].get('availableToBack') or [{}])[0]).get('price')}" for r in b["runners"]]
        print(m["event"]["name"], "|", "; ".join(px))
    print("ALL GOOD")
except urllib.error.HTTPError as e:
    print("API error:", e.code, e.read().decode()[:300])
