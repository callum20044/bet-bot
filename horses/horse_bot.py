"""Horse racing paper bots on Betfair Exchange (UK + Irish win markets). Pretend money only - never places a bet.

Runs alongside every Kalshi scan (about every 15 minutes, started by the Oracle server). Each run it:
  1. notes the prices of every GB/IE race starting in the next 90 minutes (so it can see which horses
     shorten or drift before the off)
  2. for races starting in the next 2-16 minutes, lets each bot place ONE pretend bet per race (at the best
     price really on offer, and only if enough money is waiting there)
  3. settles finished races from Betfair's result (winner / loser / non-runner = stake back)

The bots (each back bet = £10 stake; each lay bet = £10 at risk):
  fav_back       back the favourite when it is 4.0 or shorter
  steamer_back   back the horse whose price has shortened most (20%+) since we first saw it, if 10.0 or shorter
  drifter_lay    lay the horse whose price has lengthened most (30%+), if it is still 8.0 or shorter
  longshot_lay   lay the shortest outsider priced 20-40 (punters tend to over-bet long shots)
  random_back    LUCK CHECK: back a random horse. Shows what pure chance + 5% commission costs; a real bot
                 has to beat this clearly, not just be in profit for a week.

Winnings pay 5% Betfair commission. Prices come from Betfair's free delayed key, so they can be a little behind.
Files (horses branch): horses.md (report), horses_bets.csv (every settled bet), horses_state.json.
"""
import csv
import json
import math
import os
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gaps"))
import betfair  # noqa: E402  (login + API calls, never prints secrets)

STATE, LOG, REPORT = "horses_state.json", "horses_bets.csv", "horses.md"
COMM, STAKE, LIAB = 0.05, 10.0, 10.0
NOW = datetime.now(timezone.utc)
BOTS = ["fav_back", "steamer_back", "drifter_lay", "longshot_lay", "random_back"]
WHAT = {"fav_back": "back the favourite (4.0 or shorter)", "steamer_back": "back the horse being backed in (shortened 20%+)",
        "drifter_lay": "lay the horse drifting out (30%+ longer)", "longshot_lay": "lay an outsider priced 20-40",
        "random_back": "LUCK CHECK - back a random horse"}


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def best(r, side):
    """(price, £ available) of the best offer, or (None, 0)."""
    o = (r.get("ex", {}).get("availableToBack" if side == "back" else "availableToLay") or [None])[0]
    return (float(o["price"]), float(o["size"])) if o else (None, 0.0)


def decide(market, book, early):
    """Return list of (bot, selectionId, side, price) for one race about to start."""
    names = {r["selectionId"]: r["runnerName"] for r in market["runners"]}
    live = [r for r in book["runners"] if r.get("status") == "ACTIVE"]
    backs = {r["selectionId"]: best(r, "back") for r in live}
    lays = {r["selectionId"]: best(r, "lay") for r in live}
    priced = sorted((p[0], s) for s, p in backs.items() if p[0])
    if len(priced) < 4:
        return []
    bets = []

    def take_back(bot, sel):
        p, size = backs[sel]
        if p and size >= STAKE:
            bets.append((bot, sel, names.get(sel, "?"), "back", p))

    def take_lay(bot, sel):
        p, size = lays[sel]
        if p and p > 1.01 and size >= LIAB / (p - 1):
            bets.append((bot, sel, names.get(sel, "?"), "lay", p))

    fav_p, fav = priced[0]
    if fav_p <= 4.0:
        take_back("fav_back", fav)
    moves = [(backs[s][0] / early[str(s)], s) for s in backs if backs[s][0] and early.get(str(s))]
    if moves:
        m, s = min(moves)
        if m <= 0.8 and backs[s][0] <= 10:
            take_back("steamer_back", s)
        m, s = max(moves)
        if m >= 1.3 and backs[s][0] <= 8:
            take_lay("drifter_lay", s)
    outs = [(p, s) for p, s in priced if 20 <= p <= 40]
    if outs:
        take_lay("longshot_lay", outs[0][1])
    take_back("random_back", random.Random(market["marketId"]).choice([s for _, s in priced]))
    return bets


def settle(bet, status):
    if status == "REMOVED":
        return 0.0, "void"
    won_horse = status == "WINNER"
    if bet["side"] == "back":
        pnl = STAKE * (bet["price"] - 1) if won_horse else -STAKE
    else:
        pnl = -LIAB if won_horse else LIAB / (bet["price"] - 1)
    return round(pnl * (1 - COMM) if pnl > 0 else pnl, 2), ("won" if pnl > 0 else "lost")


def main():
    state = json.load(open(STATE)) if os.path.exists(STATE) else {"runs": 0, "snaps": {}, "open": [], "done": {}, "bot": {}}
    state["runs"] += 1
    try:
        bf = betfair.Betfair()
    except Exception as e:
        print(f"Betfair not reachable: {str(e)[:100]}")
        state["note"] = f"{NOW:%d %b %H:%M} UTC: Betfair not reachable - nothing done this run"
        save(state)
        return
    state["note"] = ""

    # 1) settle finished races
    mids = sorted({b["market"] for b in state["open"]})
    results = {}
    for i in range(0, len(mids), 40):
        for bk in bf.call("listMarketBook", {"marketIds": mids[i:i + 40]}):
            if bk.get("status") == "CLOSED":
                results[bk["marketId"]] = {r["selectionId"]: r.get("status") for r in bk["runners"]}
    still = []
    for b in state["open"]:
        res = results.get(b["market"])
        if res is None:
            if NOW - ts(b["start"]) > timedelta(days=2):     # abandoned / never settled: stake back
                res = {b["sel"]: "REMOVED"}
            else:
                still.append(b)
                continue
        b["pnl"], b["result"] = settle(b, res.get(b["sel"], "REMOVED"))
        b["settled"] = NOW.isoformat(timespec="minutes")
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
    settled = len(state["open"]) - len(still)
    state["open"] = still

    # 2) races in the next 90 minutes
    cat = bf.call("listMarketCatalogue", {"filter": {"eventTypeIds": ["7"], "marketCountries": ["GB", "IE"], "marketTypeCodes": ["WIN"],
                  "marketStartTime": {"from": (NOW - timedelta(minutes=5)).isoformat(), "to": (NOW + timedelta(minutes=90)).isoformat()}},
                  "maxResults": "100", "marketProjection": ["EVENT", "MARKET_START_TIME", "RUNNER_DESCRIPTION"]})
    placed = 0
    for i in range(0, len(cat), 40):
        part = cat[i:i + 40]
        books = {b["marketId"]: b for b in bf.call("listMarketBook", {"marketIds": [m["marketId"] for m in part],
                 "priceProjection": {"priceData": ["EX_BEST_OFFERS"]}})}
        for m in part:
            bk, mid = books.get(m["marketId"]), m["marketId"]
            if not bk or bk.get("status") != "OPEN" or bk.get("inplay"):
                continue
            mins = (ts(m["marketStartTime"]) - NOW).total_seconds() / 60
            snap = state["snaps"].setdefault(mid, {"start": m["marketStartTime"], "early": {}})
            if mins >= 25:                                   # "early" price = latest one seen 25+ min before the off
                for r in bk["runners"]:
                    p = best(r, "back")[0]
                    if p:
                        snap["early"][str(r["selectionId"])] = p
            if 2 <= mins <= 16:
                done = state["done"].setdefault(mid, [])
                for bot, sel, name, side, price in decide(m, bk, snap["early"]):
                    if bot in done:
                        continue
                    done.append(bot)
                    placed += 1
                    state["open"].append(dict(bot=bot, market=mid, sel=sel, horse=name, side=side, price=price,
                                              race=f"{m['event'].get('venue') or m['event']['name']} {ts(m['marketStartTime']):%H:%M}",
                                              start=m["marketStartTime"], placed=NOW.isoformat(timespec="minutes")))
    # tidy old memory
    old = (NOW - timedelta(days=2)).isoformat()
    state["snaps"] = {k: v for k, v in state["snaps"].items() if v["start"] > old}
    state["done"] = {k: v for k, v in state["done"].items() if k in state["snaps"]}
    print(f"{len(cat)} races in the next 90 min, {placed} new pretend bets, {settled} settled, {len(state['open'])} open")
    save(state)


def log(b):
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["settled", "bot", "race", "horse", "side", "price", "result", "pnl_gbp"])
        w.writerow([b["settled"], b["bot"], b["race"], b["horse"], b["side"], b["price"], b["result"], b["pnl"]])


def tscore(s):
    n = s["n"]
    if n < 2:
        return 0.0
    mean = s["r"] / n
    var = max(s["r2"] / n - mean ** 2, 1e-12) * n / (n - 1)
    return mean / math.sqrt(var / n)


def save(state):
    json.dump(state, open(STATE, "w"), indent=0)
    out = ["# Horse racing paper bots (Betfair, UK + Irish races)", "", f"Updated {NOW:%Y-%m-%d %H:%M} UTC · runs {state['runs']}", ""]
    if state.get("note"):
        out += [f"**{state['note']}**", ""]
    out += ["| Bot | What it does | Bets settled | Won | Profit (£) | Per £1 risked | t-score | Open |", "|---|---|---|---|---|---|---|---|"]
    openn = {}
    for b in state["open"]:
        openn[b["bot"]] = openn.get(b["bot"], 0) + 1
    tot = 0.0
    for bot in BOTS:
        s = state["bot"].get(bot, {"n": 0, "won": 0, "pnl": 0.0, "risk": 0.0, "r": 0.0, "r2": 0.0})
        tot += s["pnl"] if bot != "random_back" else 0
        roi = s["pnl"] / s["risk"] if s["risk"] else 0
        out.append(f"| {bot} | {WHAT[bot]} | {s['n']} | {s['won']} | {s['pnl']:+.2f} | {roi:+.3f} | {tscore(s):+.1f} | {openn.get(bot, 0)} |")
    out += ["", f"Real bots combined (not counting the luck check): **£{tot:+.2f}**", "",
            "Back bets risk £10, lay bets risk £10. Winnings pay 5% commission. Pretend money only.",
            "Rule of thumb: ignore anything under ~300 bets; a bot needs t >= 2 AND to beat the luck check clearly."]
    open(REPORT, "w").write("\n".join(out) + "\n")
    print("\n".join(out[:12]))


if __name__ == "__main__":
    main()
