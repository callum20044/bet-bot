"""Football paper bots on Betfair Exchange - the Kalshi winners moved to a market UK residents can use.
Pretend money only - never places a bet. Runs with the horse/dog bots every ~15 minutes.

  value_back     (the Betfair version of sharp_spread / the Pinnacle bots) back a result when Betfair's price is
                 better than Pinnacle's "true" odds (Pinnacle = the sharp bookie, margin stripped out) by 2%+ after
                 Betfair's 5% commission. Uses the Pinnacle odds the Kalshi bot already downloads (no extra credits),
                 only while they are under 45 minutes old, and ignores gaps over 15% (those are stale prices).
                 Premier League + Champions League.
  underdog_lay   (the Betfair version of underdog_long) 3-6 days before kick-off, lay a result priced 3.3-6.7
                 (a 15-30% chance) - Kalshi's best bot bet against those. Big European leagues.
  random_back    LUCK CHECK: back a random result in each underdog_lay match.

Back bets stake £10; lay bets risk £10. Files (football branch): football.md, football_bets.csv, football_state.json.
"""
import csv
import json
import math
import os
import random
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gaps"))
import betfair  # noqa: E402

STATE, LOG, REPORT = "football_state.json", "football_bets.csv", "football.md"
COMM, STAKE, LIAB = 0.05, 10.0, 10.0
NOW = datetime.now(timezone.utc)
BOTS = ["value_back", "underdog_lay", "random_back"]
WHAT = {"value_back": "back when Betfair beats Pinnacle's true odds (2%+)",
        "underdog_lay": "lay a 15-30% chance, 3-6 days out", "random_back": "LUCK CHECK - back a random result"}
COMPS = {"English Premier League": ["english", "premier league"], "Champions League": ["uefa champions league"],
         "Championship": ["english", "championship"], "La Liga": ["spanish", "la liga"], "Serie A": ["italian", "serie a"],
         "Bundesliga": ["german", "bundesliga"], "Ligue 1": ["french", "ligue 1"], "Scottish Prem": ["scottish", "premiership"]}
VALUE_SPORTS = {"soccer_epl": "English Premier League", "soccer_uefa_champs_league": "Champions League"}
PIN_MAX_AGE_MIN, MIN_EV, MAX_EV = 45, 0.02, 0.15


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def pinnacle(db="predbot.db"):
    """Pinnacle events cached by the Kalshi bot, or [] if missing / too old."""
    try:
        row = sqlite3.connect(db).execute("SELECT v FROM meta WHERE k='odds_cache'").fetchone()
        c = json.loads(row[0])
    except Exception:
        return [], None
    age = (time.time() - c["t"]) / 60
    return (c["events"] if age <= PIN_MAX_AGE_MIN else []), round(age)


def fair(ev):
    for b in ev.get("bookmakers", []):
        for mk in b.get("markets", []):
            if mk.get("key") == "h2h":
                inv = {o["name"]: 1 / o["price"] for o in mk["outcomes"] if o.get("price")}
                tot = sum(inv.values())
                return {k: v / tot for k, v in inv.items()} if tot and len(inv) == 3 else {}
    return {}


def best(r, side):
    o = (r.get("ex", {}).get("availableToBack" if side == "back" else "availableToLay") or [None])[0]
    return (float(o["price"]), float(o["size"])) if o else (None, 0.0)


def settle(bet, status):
    if status == "REMOVED":
        return 0.0, "void"
    won = status == "WINNER"
    if bet["side"] == "back":
        pnl = STAKE * (bet["price"] - 1) if won else -STAKE
    else:
        pnl = -LIAB if won else LIAB / (bet["price"] - 1)
    return round(pnl * (1 - COMM) if pnl > 0 else pnl, 2), ("won" if pnl > 0 else "lost")


def main():
    state = json.load(open(STATE)) if os.path.exists(STATE) else {"runs": 0, "open": [], "done": {}, "bot": {}}
    state["runs"] += 1
    try:
        bf = betfair.Betfair()
    except Exception as e:
        state["note"] = f"{NOW:%d %b %H:%M} UTC: Betfair not reachable - nothing done this run"
        print(state["note"], str(e)[:80])
        return save(state)
    state["note"] = ""

    # 1) settle
    mids = sorted({b["market"] for b in state["open"]})
    res = {}
    for i in range(0, len(mids), 40):
        for bk in bf.call("listMarketBook", {"marketIds": mids[i:i + 40]}):
            if bk.get("status") == "CLOSED":
                res[bk["marketId"]] = {r["selectionId"]: r.get("status") for r in bk["runners"]}
    still, settled = [], 0
    for b in state["open"]:
        r = res.get(b["market"])
        if r is None and NOW - ts(b["start"]) > timedelta(days=4):
            r = {}
        if r is None:
            still.append(b)
            continue
        b["pnl"], b["result"] = settle(b, r.get(b["sel"], "REMOVED"))
        b["settled"] = NOW.isoformat(timespec="minutes")
        settled += 1
        log(b)
        if b["result"] != "void":
            s = state["bot"].setdefault(b["bot"], {"n": 0, "won": 0, "pnl": 0.0, "risk": 0.0, "r": 0.0, "r2": 0.0})
            risk = STAKE if b["side"] == "back" else LIAB
            s["n"] += 1
            s["won"] += b["result"] == "won"
            s["pnl"] = round(s["pnl"] + b["pnl"], 2)
            s["risk"] += risk
            s["r"] += b["pnl"] / risk
            s["r2"] += (b["pnl"] / risk) ** 2
    state["open"] = still

    # 2) upcoming matches in the chosen competitions
    comps = bf.call("listCompetitions", {"filter": {"eventTypeIds": ["1"]}})
    cid = {}
    for c in comps:
        n = c["competition"]["name"].lower()
        for label, words in COMPS.items():
            if all(w in n for w in words) and "women" not in n and "u21" not in n:
                cid[c["competition"]["id"]] = label
    cat = bf.call("listMarketCatalogue", {"filter": {"competitionIds": list(cid), "marketTypeCodes": ["MATCH_ODDS"],
                  "marketStartTime": {"from": (NOW + timedelta(minutes=15)).isoformat(), "to": (NOW + timedelta(days=6)).isoformat()}},
                  "maxResults": "300", "marketProjection": ["EVENT", "COMPETITION", "MARKET_START_TIME", "RUNNER_DESCRIPTION"]})
    pin, pin_age = pinnacle()
    state["pin_age"] = pin_age
    placed = 0
    for i in range(0, len(cat), 40):
        part = cat[i:i + 40]
        books = {b["marketId"]: b for b in bf.call("listMarketBook", {"marketIds": [m["marketId"] for m in part],
                 "priceProjection": {"priceData": ["EX_BEST_OFFERS"]}})}
        for m in part:
            bk, mid = books.get(m["marketId"]), m["marketId"]
            if not bk or bk.get("status") != "OPEN" or bk.get("inplay"):
                continue
            runners = sorted(m["runners"], key=lambda r: r.get("sortPriority", 9))
            if len(runners) != 3:
                continue
            name = {r["selectionId"]: r["runnerName"] for r in runners}
            book = {r["selectionId"]: r for r in bk["runners"]}
            hours = (ts(m["marketStartTime"]) - NOW).total_seconds() / 3600
            comp = cid.get((m.get("competition") or {}).get("id"), "?")
            done = state["done"].setdefault(mid, [])
            label = f"{m['event']['name']} ({comp}, {ts(m['marketStartTime']):%a %d %b %H:%M})"

            def add(bot, sel, side, price):
                nonlocal placed
                done.append(bot)
                placed += 1
                state["open"].append(dict(bot=bot, market=mid, sel=sel, pick=name[sel], side=side, price=price, match=label,
                                          start=m["marketStartTime"], placed=NOW.isoformat(timespec="minutes")))

            # underdog_lay + its luck check: once per match, 72-144h out
            if 72 <= hours <= 144 and "underdog_lay" not in done and "random_back" not in done:
                cands = []
                for s in name:
                    p, size = best(book.get(s, {}), "lay")
                    if p and 3.33 <= p <= 6.67 and size >= LIAB / (p - 1):
                        cands.append((p, s))
                if cands:
                    p, s = min(cands)
                    add("underdog_lay", s, "lay", p)
                s = random.Random(mid).choice(list(name))
                p, size = best(book.get(s, {}), "back")
                if p and size >= STAKE:
                    add("random_back", s, "back", p)
            # value_back vs Pinnacle
            if pin and comp in VALUE_SPORTS.values() and "value_back" not in done:
                ev = next((e for e in pin if VALUE_SPORTS.get(e.get("sport_key")) == comp
                           and abs((ts(e["commence_time"]) - ts(m["marketStartTime"])).total_seconds()) <= 3600
                           and betfair.same_team(e["home_team"], runners[0]["runnerName"])
                           and betfair.same_team(e["away_team"], runners[1]["runnerName"])), None)
                probs = fair(ev) if ev else {}
                if probs:
                    ours = {runners[0]["selectionId"]: probs.get(ev["home_team"]), runners[1]["selectionId"]: probs.get(ev["away_team"]),
                            runners[2]["selectionId"]: probs.get("Draw")}
                    picks = []
                    for s, q in ours.items():
                        p, size = best(book.get(s, {}), "back")
                        if q and p and size >= STAKE:
                            e = q * (p - 1) * (1 - COMM) - (1 - q)
                            if MIN_EV <= e <= MAX_EV:
                                picks.append((e, s, p))
                    if picks:
                        e, s, p = max(picks)
                        add("value_back", s, "back", p)
    old = (NOW - timedelta(days=8)).isoformat()
    open_m = {b["market"] for b in state["open"]}
    state["done"] = {k: v for k, v in state["done"].items() if k in open_m or k in {m["marketId"] for m in cat}}
    print(f"{len(cat)} matches on Betfair ({len(cid)} competitions), Pinnacle odds age {pin_age} min, "
          f"{placed} new pretend bets, {settled} settled, {len(state['open'])} open")
    save(state)


def log(b):
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["settled", "bot", "match", "pick", "side", "price", "result", "pnl_gbp"])
        w.writerow([b["settled"], b["bot"], b["match"], b["pick"], b["side"], b["price"], b["result"], b["pnl"]])


def tscore(s):
    n = s["n"]
    if n < 2:
        return 0.0
    mean = s["r"] / n
    var = (s["r2"] / n - mean ** 2) * n / (n - 1)
    return mean / math.sqrt(var / n) if var > 1e-9 else 0.0     # all bets the same result: no spread to judge


def save(state):
    json.dump(state, open(STATE, "w"), indent=0)
    out = ["# Football paper bots on Betfair (Kalshi winners moved to Betfair)", "", f"Updated {NOW:%Y-%m-%d %H:%M} UTC · runs {state['runs']}", ""]
    if state.get("note"):
        out += [f"**{state['note']}**", ""]
    out += ["| Bot | What it does | Bets settled | Won | Profit (£) | Per £1 risked | t-score | Open |", "|---|---|---|---|---|---|---|---|"]
    openn = {}
    for b in state["open"]:
        openn[b["bot"]] = openn.get(b["bot"], 0) + 1
    for bot in BOTS:
        s = state["bot"].get(bot, {"n": 0, "won": 0, "pnl": 0.0, "risk": 0.0, "r": 0.0, "r2": 0.0})
        roi = s["pnl"] / s["risk"] if s["risk"] else 0
        out.append(f"| {bot} | {WHAT[bot]} | {s['n']} | {s['won']} | {s['pnl']:+.2f} | {roi:+.3f} | {tscore(s):+.1f} | {openn.get(bot, 0)} |")
    out += ["", "Back bets stake £10, lay bets risk £10, winnings pay 5% commission. Pretend money only.",
            "Rule of thumb: ignore anything under ~300 bets; a bot needs t >= 2 AND to beat the luck check clearly."]
    open(REPORT, "w").write("\n".join(out) + "\n")
    print("\n".join(out[:10]))


if __name__ == "__main__":
    main()
