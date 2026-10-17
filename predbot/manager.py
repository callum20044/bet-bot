"""The bot manager: once a day it reviews every bot and switches off the clear losers, gives proven
winners more room, and promotes shadow variants that have proven themselves.

Rules are deliberately strict, because with dozens of bots some will look good by luck:
  * judged on return per $1 bet after fees, with a t-score (how many standard errors from zero)
  * switch OFF: 30+ settled bets and t below -1.5 (clearly losing), or 80+ bets and still in the red
  * PROVEN:     300+ settled bets, in profit, t at least 2  -> 1.5x the open-bet limit (max 120)
  * shadow variant PROMOTED to real bets: 300+ shadow bets, in profit, t at least 2.5
Every decision is written to the 'manager_log' (and the scan log) with the numbers behind it.
"""
import json
import math
from datetime import datetime

from . import config

KILL_MIN_N, KILL_T = 30, -1.5
KILL_RED_N = 80
PROVE_N, PROVE_T = 300, 2.0
VARIANT_PROVE_T = 2.5            # stricter for variants: with ~100 tested, a few will hit t=2 by luck
NEVER_TOUCH = {"arb"}            # near risk-free baskets: judged by hand


def stats(rows):
    """rows of (pnl, cost) -> n, net pnl, return per $1, t-score."""
    rets = [p / c for p, c in rows if c]
    n = len(rets)
    net = sum(p for p, _ in rows)
    if n < 2:
        return n, net, (rets[0] if rets else 0.0), 0.0
    mean = sum(rets) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1)) or 1e-9
    return n, net, mean, mean / (sd / math.sqrt(n))


def _meta(db, k, default):
    row = db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return json.loads(row[0]) if row else default


def _set(db, k, v):
    db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, json.dumps(v)))


def apply_overrides(db):
    """Switch-offs and limit changes the manager made, applied on top of config every scan."""
    for name, o in _meta(db, "overrides", {}).items():
        block = getattr(config, config.STRATEGY_CONFIG.get(name, ""), None)
        if block is not None:
            block.update(o)


def review(db, now):
    if _meta(db, "manager_last", "") == now.date().isoformat():
        return
    overrides = _meta(db, "overrides", {})
    log = _meta(db, "manager_log", [])
    notes = []

    def note(msg):
        notes.append(msg)
        log.append(f"{now:%Y-%m-%d} {msg}")

    # ---- real bots
    for name in sorted(config.enabled_strategies() - NEVER_TOUCH):
        rows = db.execute("SELECT payout - cost - fee, cost FROM trades WHERE strategy=? AND status IN ('won','lost')",
                          (name,)).fetchall()
        n, net, mean, t = stats([(r[0], r[1]) for r in rows])
        tag = f"{name}: {n} bets, ${net:+.2f}, {mean*100:+.1f}p per $1, t={t:+.1f}"
        if (n >= KILL_MIN_N and t <= KILL_T) or (n >= KILL_RED_N and net < 0):
            overrides.setdefault(name, {})["enabled"] = False
            note(f"SWITCHED OFF {tag}")
        elif n >= PROVE_N and net > 0 and t >= PROVE_T and not overrides.get(name, {}).get("proven"):
            block = getattr(config, config.STRATEGY_CONFIG[name])
            new_cap = min(120, int(block.get("max_open", config.MAX_OPEN_PER_STRATEGY) * 1.5))
            overrides.setdefault(name, {}).update(max_open=new_cap, proven=True)
            note(f"PROVEN {tag} -> open-bet limit raised to {new_cap}")

    # ---- shadow variants
    from . import shadow
    db.execute(shadow.SCHEMA)
    variants = _meta(db, "variants", {})
    for v in shadow.VARIANTS:
        st = variants.setdefault(v["name"], {"status": "testing"})
        if st["status"] != "testing":
            continue
        rows = db.execute("SELECT payout - cost - fee, cost FROM shadow_trades WHERE variant=? AND status IN ('won','lost')",
                          (v["name"],)).fetchall()
        n, net, mean, t = stats([(r[0], r[1]) for r in rows])
        tag = f"variant {v['name']}: {n} shadow bets, ${net:+.2f}, {mean*100:+.1f}p per $1, t={t:+.1f}"
        if (n >= KILL_MIN_N and t <= KILL_T) or (n >= KILL_RED_N and net < 0):
            st["status"] = "dropped"
            note(f"DROPPED {tag}")
        elif n >= PROVE_N and net > 0 and t >= VARIANT_PROVE_T:
            st["status"] = "live"
            note(f"PROMOTED {tag} -> now placing real bets as 'v:{v['name']}'")

    _set(db, "overrides", overrides)
    _set(db, "variants", variants)
    _set(db, "manager_log", log[-300:])
    _set(db, "manager_last", now.date().isoformat())
    db.commit()
    apply_overrides(db)
    print("  manager: " + ("; ".join(notes) if notes else "daily review - no changes"))
