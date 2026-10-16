"""Replacements (added 16 Oct): stretch the three bots that are actually making money.

  underdog_long  - underdog fade (NO on YES 15-30c) for markets settling 3-6 days out
                   (the original only looks 3 days ahead).
  longshot_long  - longshot fade (NO on YES 3-15c), same 3-6 day window.
  ladder3        - ladder smoothing for 3-rung ladders, which the original (4+ rungs) skips.
"""
from . import config
from .extra import _edge, ladder_smooth
from .models import Signal


def _fade(m, now, c, name):
    if not c["enabled"] or m.yes_mid is None or m.no_ask is None:
        return None
    h = m.hours_to_close(now)
    if h is None or not (c["min_hours_to_close"] < h <= c["max_hours_to_close"]):
        return None
    mid = round(m.yes_mid, 4)
    if not (c["yes_min"] <= mid <= c["yes_max"]):
        return None
    est_no = 1 - mid * c["shrink"]
    e = _edge(est_no, m.no_ask)
    if e < c["min_edge"]:
        return None
    return Signal(m, "no", m.no_ask, est_no, e, name, f"YES at {mid*100:.0f}c, {h:.0f}h out; est NO {est_no*100:.1f}%")


def underdog_long(m, now, broker):
    return _fade(m, now, config.UNDERDOG_LONG, "underdog_long")


def longshot_long(m, now, broker):
    return _fade(m, now, config.LONGSHOT_LONG, "longshot_long")


PER_MARKET = [underdog_long, longshot_long]


def group_signals(tradable, now):
    c = config.LADDER3
    return ladder_smooth(tradable, now, c=c, name="ladder3", exact_rungs=3) if c["enabled"] else []
