"""Betfair vs bookies gap bot (paper mode, pretend money only).

Every run it pulls football odds from The Odds API (UK region: Bet365, Sky Bet, William Hill, Paddy Power,
Betfair Exchange back + lay prices, etc.) and looks for two kinds of locked-in profit:

  BACK/LAY : back a result at a bookie, lay the same result on Betfair Exchange (5% commission on winnings)
  BOOKIES  : back all three results (home / draw / away) at different bookies so every outcome pays

Each gap is sized as if we had £100 to put on the bookie side, and the guaranteed profit is logged.
Paper only: nothing is ever bet. Needs secret ODDS_API_KEY (free tier = 500 credits a month); the bot
reads the remaining credits from the API and skips runs when they run low. Never prints the key.
Caveat: the API gives no Betfair liquidity, so small gaps may not have had enough money behind them.
Files (gaps branch): gaps.md (report), gaps_log.csv (every gap), gaps_state.json.
"""
import csv
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SPORTS = ["soccer_epl", "soccer_efl_champ", "soccer_uefa_champs_league"]
COMM = 0.05            # Betfair Exchange commission on net winnings
STAKE = 100.0          # pretend bookie-side stake per gap
MIN_PCT = 0.5          # only log gaps worth at least 0.5% of the stake
MAX_ODDS = 15.0        # ignore long shots (stale prices, tiny liquidity)
KEEP_CREDITS = 40      # stop using the free allowance when it gets this low
STATE, LOG, REPORT = "gaps_state.json", "gaps_log.csv", "gaps.md"
NOW = datetime.now(timezone.utc)


def odds(sport, key):
    q = urllib.parse.urlencode({"apiKey": key, "regions": "uk", "markets": "h2h,h2h_lay", "oddsFormat": "decimal"})
    req = urllib.request.Request(f"https://api.the-odds-api.com/v4/sports/{sport}/odds?{q}")
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r), int(float(r.headers.get("x-requests-remaining", "0") or 0))


def back_lay(back, lay):
    """Lay stake that gives the same profit either way, and that profit."""
    ls = STAKE * back / (lay - COMM)
    return ls, -STAKE + ls * (1 - COMM)


def scan(game):
    """Return a list of gaps for one match."""
    gaps = []
    best = {}                        # outcome -> (odds, bookie) best bookie back price
    lays = {}                        # outcome -> Betfair lay price
    for bk in game.get("bookmakers", []):
        is_ex = bk["key"].startswith("betfair_ex")
        for m in bk.get("markets", []):
            for o in m.get("outcomes", []):
                p = float(o["price"])
                if not 1.01 < p <= MAX_ODDS:
                    continue
                if m["key"] == "h2h_lay":
                    if is_ex:
                        lays[o["name"]] = p
                elif m["key"] == "h2h" and not is_ex:
                    if p > best.get(o["name"], (0, ""))[0]:
                        best[o["name"]] = (p, bk["title"])
    for name, (b, who) in best.items():
        if name in lays:
            ls, pr = back_lay(b, lays[name])
            if pr / STAKE * 100 >= MIN_PCT:
                gaps.append(dict(kind="back/lay", pick=name, detail=f"back {b} @ {who}, lay {lays[name]} @ Betfair (lay £{ls:.2f})",
                                 profit=pr))
    if len(best) == 3:
        inv = sum(1 / v[0] for v in best.values())
        if inv < 1:
            total = STAKE * 3                      # split £300 across the three results
            pr = total / inv - total
            if pr / total * 100 >= MIN_PCT:
                parts = ", ".join(f"{n} {v[0]} @ {v[1]} (£{total / v[0] / inv:.0f})" for n, v in best.items())
                gaps.append(dict(kind="bookies", pick="all 3 results", detail=parts, profit=pr))
    return gaps


def main():
    key = os.environ.get("ODDS_API_KEY", "").strip()
    state = json.load(open(STATE)) if os.path.exists(STATE) else {"runs": 0, "seen": {}, "credits": None, "matches": 0}
    if not key:
        print("ODDS_API_KEY not set - nothing to do yet")
    elif state.get("credits") is not None and state["credits"] < KEEP_CREDITS and NOW.day != 1:
        print(f"only {state['credits']} free credits left this month - skipping until they reset")
    else:
        new = 0
        for sport in SPORTS:
            try:
                games, left = odds(sport, key)
            except urllib.error.HTTPError as e:
                print(f"{sport}: HTTP {e.code}")
                continue
            state["credits"] = left
            for g in games:
                kick = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))
                if kick < NOW:                     # in-play prices move too fast to trust
                    continue
                state["matches"] = state.get("matches", 0) + 1
                for gap in scan(g):
                    k = f"{g['id']}|{gap['kind']}|{gap['pick']}"
                    first = k not in state["seen"]
                    rec = state["seen"].setdefault(k, dict(match=f"{g['home_team']} v {g['away_team']}", sport=sport,
                                                           kickoff=g["commence_time"], kind=gap["kind"], pick=gap["pick"],
                                                           first=NOW.isoformat(), best=0, times=0))
                    rec["times"] += 1
                    rec["last"] = NOW.isoformat()
                    if gap["profit"] > rec["best"]:
                        rec["best"], rec["detail"] = round(gap["profit"], 2), gap["detail"]
                    if first:
                        new += 1
                        log([NOW.isoformat(timespec="minutes"), sport, rec["match"], gap["kind"], gap["pick"],
                             round(gap["profit"], 2), gap["detail"]])
            print(f"{sport}: {len(games)} matches, credits left {state['credits']}")
        state["runs"] += 1
        print(f"{new} new gaps")
    json.dump(state, open(STATE, "w"), indent=1)
    report(state, bool(key))


def log(row):
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "sport", "match", "kind", "pick", "profit_gbp", "detail"])
        w.writerow(row)


def report(state, has_key):
    seen = list(state["seen"].values())
    bl = [s for s in seen if s["kind"] == "back/lay"]
    bk = [s for s in seen if s["kind"] == "bookies"]
    repeat = [s for s in seen if s["times"] >= 2]
    out = ["# Betfair vs bookies gap bot (paper mode)", "", f"Updated {NOW:%Y-%m-%d %H:%M} UTC.", ""]
    if not has_key:
        out += ["**Waiting for the ODDS_API_KEY secret.**"]
    out += [f"Runs: {state['runs']} · match checks: {state.get('matches', 0)} · free credits left this month: {state.get('credits')}", "",
            f"**Gaps found: {len(seen)}** ({len(bl)} back/lay, {len(bk)} bookie-vs-bookie) · "
            f"pretend locked-in profit if each was taken once: **£{sum(s['best'] for s in seen):.2f}**", "",
            f"Still there on a later check (more likely to be real, not a stale price): {len(repeat)}", "",
            "| First seen | Match | Type | Pick | Best profit | Times seen | Detail |", "|---|---|---|---|---|---|---|"]
    for s in sorted(seen, key=lambda s: s["first"], reverse=True)[:40]:
        out.append(f"| {s['first'][5:16].replace('T', ' ')} | {s['match']} | {s['kind']} | {s['pick']} | £{s['best']:.2f} | "
                   f"{s['times']} | {s.get('detail', '')} |")
    out += ["", "Profit is per £100 on the bookie side (bookie-vs-bookie: per £300 split over the 3 results), after 5% Betfair commission.",
            "Paper only - nothing is bet. No Betfair liquidity data, so treat small gaps with caution."]
    open(REPORT, "w").write("\n".join(out) + "\n")
    print("\n".join(out[:10]))


if __name__ == "__main__":
    main()
