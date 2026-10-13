"""Inflation nowcast: Kalshi CPI markets vs the Cleveland Fed's daily inflation estimate.

The Cleveland Fed publishes a free 'nowcast' of the upcoming CPI figures, updated every
weekday. Kalshi asks things like "will CPI rise more than 0.5% in September?". We treat the
nowcast as the centre of a bell curve (its typical miss is about 0.1 percentage points) and
bet when Kalshi's price disagrees by enough, only in the last couple of days before release.
"""
import json
import math
import re
import urllib.request

from . import config
from .fees import fee_per_contract
from .models import Signal

URL = "https://www.clevelandfed.org/-/media/files/webcharts/inflationnowcasting/nowcast_{}.json"
# Kalshi series -> (nowcast file, series name in that file)
SERIES = {
    "KXCPI": ("month", "CPI Inflation"),
    "KXCPICORE": ("month", "Core CPI Inflation"),
    "KXCPIYOY": ("year", "CPI Inflation"),
    "KXCPICOREYOY": ("year", "Core CPI Inflation"),
}
MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def fetch(kind):
    req = urllib.request.Request(URL.format(kind), headers={
        "User-Agent": "Mozilla/5.0 (predbot paper trading)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def latest(blocks, month_key, name):
    """Most recent nowcast value for e.g. month_key '2026-9', series 'CPI Inflation'."""
    for b in blocks:
        if b.get("chart", {}).get("subcaption") == month_key:
            for s in b.get("dataset", []):
                if s.get("seriesname") == name and s.get("data"):
                    return float(s["data"][-1]["value"])
    return None


def month_key(event_ticker):
    m = re.search(r"-(\d{2})([A-Z]{3})$", event_ticker)
    if not m or m.group(2) not in MONTHS:
        return None
    return f"20{m.group(1)}-{MONTHS[m.group(2)]}"


def prob(raw, mu, sd):
    """Figures are published to one decimal, so 'above 0.5%' means a print of 0.6% or more."""
    st = raw.get("strike_type")
    if st == "greater":
        return 1 - norm_cdf((raw["floor_strike"] + 0.05 - mu) / sd)
    if st == "less":
        return norm_cdf((raw["cap_strike"] - 0.05 - mu) / sd)
    if st == "between":
        return norm_cdf((raw["cap_strike"] + 0.05 - mu) / sd) - norm_cdf((raw["floor_strike"] - 0.05 - mu) / sd)
    return None


def signals(source, now, broker, ok=lambda m: True, fetcher=fetch):
    c = config.NOWCAST
    if not c["enabled"] or not hasattr(source, "list_series_markets"):
        return []
    data, out = {}, []
    for series, (kind, name) in SERIES.items():
        try:
            markets = source.list_series_markets(series)
        except Exception as e:
            print(f"  ! kalshi {series}: {e}")
            continue
        for m in markets:
            hrs = m.hours_to_close(now)
            if hrs is None or not (0 < hrs <= c["max_hours_to_release"]) or not ok(m) or not m.raw:
                continue
            key = month_key(m.event_ticker)
            if kind not in data:
                try:
                    data[kind] = fetcher(kind)
                except Exception as e:
                    print(f"  ! cleveland fed nowcast: {e}")
                    data[kind] = None
            if not data[kind] or not key:
                continue
            mu = latest(data[kind], key, name)
            if mu is None:
                continue
            p = prob(m.raw, mu, c["sd"][kind])
            if p is None:
                continue
            p = min(max(p, 0.01), 0.99)
            best = None
            for side, pr, price in (("yes", p, m.yes_ask), ("no", 1 - p, m.no_ask)):
                if price is None or not (c["price_min"] <= price <= c["price_max"]):
                    continue
                edge = pr - price - fee_per_contract(price)
                if edge >= c["min_edge"] and (best is None or edge > best[3]):
                    best = (side, pr, price, edge)
            if best:
                side, pr, price, edge = best
                out.append(Signal(m, side, price, pr, edge, "nowcast",
                                  f"Fed nowcast {mu:.2f}% -> YES {p*100:.0f}% vs {m.yes_ask*100:.0f}c"))
    return out
