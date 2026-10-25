"""Replacements (added 16 Oct): stretch the three bots that are actually making money.

  underdog_long  - underdog fade (NO on YES 15-30c) for markets settling 3-6 days out
                   (the original only looks 3 days ahead).
  longshot_long  - longshot fade (NO on YES 3-15c), same 3-6 day window.
  ladder3        - ladder smoothing for 3-rung ladders, which the original (4+ rungs) skips.

Added 19 Oct (weekly review):
  longshot_prematch - longshot fade (NO on YES 3-15c, up to 3 days out) that skips sport markets in their
                   last 6 hours. In-play longshot prices already know the score: longshot_fade lost $41 on
                   173 such bets while making $65 on 195 sport bets placed 6h+ out and $49 on 132 non-sport.

Added 25 Oct:
  underdog_focus - underdog fade on non-sport markets only (the half of underdog that's doing best).
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


def longshot_prematch(m, now, broker):
    c = config.LONGSHOT_PRE
    if not c["enabled"] or m.yes_mid is None or m.no_ask is None:
        return None
    h = m.hours_to_close(now)
    if h is None or not (0 < h <= c["max_hours_to_close"]):
        return None
    from .shadow import is_sport
    sport = is_sport(m)
    if sport and h < c["min_hours_sport"]:
        return None
    mid = round(m.yes_mid, 4)
    if not (c["yes_min"] <= mid <= c["yes_max"]):
        return None
    est_no = 1 - mid * c["shrink"]
    e = _edge(est_no, m.no_ask)
    if e < c["min_edge"]:
        return None
    return Signal(m, "no", m.no_ask, est_no, e, "longshot_prematch",
                  f"YES at {mid*100:.0f}c, {h:.0f}h out{' (pre-match)' if sport else ''}; est NO {est_no*100:.1f}%")


def underdog_focus(m, now, broker):
    """(25 Oct) underdog fade, non-sport markets only - see config.UNDERDOG_FOCUS."""
    from .shadow import is_sport
    series = m.event_ticker.split("-")[0]
    if is_sport(m) or any(w in series for w in ("MAP", "BTTS", "GAME", "MATCH", "SPREAD", "TOTAL", "GOAL", "SET", "ROUND")):
        return None
    return _fade(m, now, config.UNDERDOG_FOCUS, "underdog_focus")


PER_MARKET = [underdog_long, longshot_long, longshot_prematch, underdog_focus]


def group_signals(tradable, now):
    c = config.LADDER3
    return ladder_smooth(tradable, now, c=c, name="ladder3", exact_rungs=3) if c["enabled"] else []
