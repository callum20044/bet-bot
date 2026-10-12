"""Weather strategy: Kalshi's daily high-temperature markets vs the official US weather forecast.

Each US city has ~6 markets a day ("71-72°", "77° or above", ...) settled on the official
climate report for one weather station. We take the National Weather Service forecast high
for that station, turn it into a probability for every bracket, and add what the station has
already recorded today (the high can't end up lower than a temperature already reached).
When our probability and Kalshi's price disagree by enough to beat fees, we bet.
"""
import json
import math
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config
from .fees import fee_per_contract
from .models import Signal

NWS = "https://api.weather.gov"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless", "gem_seamless",
          "jma_seamless", "ukmo_seamless", "meteofrance_seamless"]
UA = {"User-Agent": "predbot paper-trading (github.com)", "Accept": "application/geo+json"}

# Climate-report site code (the "CLIxxx" in Kalshi's rules) -> (station, latitude, longitude)
STATIONS = {
    "NYC": ("KNYC", 40.7789, -73.9692),   # Central Park
    "MDW": ("KMDW", 41.7868, -87.7522),   # Chicago Midway
    "MIA": ("KMIA", 25.7881, -80.3169),
    "LAX": ("KLAX", 33.9382, -118.3866),
    "AUS": ("KAUS", 30.1945, -97.6699),
    "DEN": ("KDEN", 39.8467, -104.6562),
    "PHL": ("KPHL", 39.8733, -75.2268),
    "ATL": ("KATL", 33.6407, -84.4277),
    "BOS": ("KBOS", 42.3606, -71.0097),
    "DCA": ("KDCA", 38.8483, -77.0342),
    "SEA": ("KSEA", 47.4447, -122.3136),
    "SFO": ("KSFO", 37.6197, -122.3647),
    "PHX": ("KPHX", 33.4343, -112.0116),
    "LAS": ("KLAS", 36.0719, -115.1634),
    "MSP": ("KMSP", 44.8848, -93.2223),
    "OKC": ("KOKC", 35.3931, -97.6007),
    "MSY": ("KMSY", 29.9934, -90.2580),
    "SAT": ("KSAT", 29.5337, -98.4698),
    "HOU": ("KHOU", 29.6454, -95.2789),   # Houston Hobby
    "IAH": ("KIAH", 29.9844, -95.3414),
    "DFW": ("KDFW", 32.8998, -97.0403),
    "DAL": ("KDAL", 32.8471, -96.8518),   # Dallas Love Field
    "SAN": ("KSAN", 32.7336, -117.1831),
    "EWR": ("KEWR", 40.6925, -74.1687),
    "TTN": ("KTTN", 40.2767, -74.8135),
}

MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


# ---------------- pure maths (unit-tested) ----------------

def c_to_f(c):
    return c * 9 / 5 + 32


def norm_cdf(x, mu, sigma):
    return 0.5 * (1 + math.erf((x - mu) / (sigma * math.sqrt(2))))


def high_cdf(t, mu, sigma, floor=None):
    """P(final high < t) when the high is max(floor, X) and X ~ Normal(mu, sigma)."""
    if floor is not None and t <= floor:
        return 0.0
    return norm_cdf(t, mu, sigma)


def bracket_prob(strike_type, lo, hi, mu, sigma, floor=None):
    """Probability the reported (whole-degree) high lands in a Kalshi bracket.
    between lo..hi -> lo <= high <= hi ; greater s -> high > s ; less s -> high < s."""
    F = lambda t: high_cdf(t, mu, sigma, floor)
    if strike_type == "between":
        return max(0.0, F(hi + 0.5) - F(lo - 0.5))
    if strike_type == "greater":
        return 1 - F(lo + 0.5)
    if strike_type == "less":
        return F(hi - 0.5)
    raise ValueError(strike_type)


def parse_event_date(event_ticker):
    """KXHIGHTATL-26OCT13 -> date(2026, 10, 13)"""
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})$", event_ticker or "")
    if not m or m.group(2) not in MONTHS:
        return None
    return datetime(2000 + int(m.group(1)), MONTHS[m.group(2)], int(m.group(3))).date()


def site_code(rules):
    m = re.search(r"\(CLI([A-Z]{3})\)", rules or "")
    return m.group(1) if m else None


def climate_day_utc(day, tz):
    """Climate reports use local STANDARD time all year, so in summer the 'day' runs 1am-1am."""
    probe = datetime(day.year, day.month, day.day, 12, tzinfo=tz)
    std_offset = probe.utcoffset() - (probe.dst() or timedelta(0))
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - std_offset
    return start, start + timedelta(days=1)


def spread(values):
    """Standard deviation of the forecast models' highs (how much they disagree)."""
    v = [x for x in values if x is not None]
    if len(v) < 3:
        return None
    m = sum(v) / len(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def model(fc_high_f, now_local, day, obs_max_f, c, model_spread=None):
    """Return (mu, sigma, floor) for the final high, in °F.
    sigma combines the forecast's usual error for that lead time with how much the
    independent weather models disagree today."""
    days_ahead = (day - now_local.date()).days
    floor = None
    if days_ahead <= 0:
        if obs_max_f is not None:
            floor = obs_max_f - c["obs_slack_f"]
        if now_local.hour >= c["day_done_hour"] and obs_max_f is not None:
            return obs_max_f, c["sigma_done"], floor          # the high has almost certainly happened
        base = c["sigma_today"]
    elif days_ahead == 1:
        base = c["sigma_tomorrow"]
    else:
        base = c["sigma_later"]
    sp = model_spread if model_spread is not None else c["default_spread"]
    sigma = math.sqrt(base ** 2 + (c["spread_weight"] * sp) ** 2)
    return fc_high_f + c["bias_f"], sigma, floor


# ---------------- data fetching ----------------

def _nws(path_or_url):
    url = path_or_url if path_or_url.startswith("http") else NWS + path_or_url
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                return json.load(r)
        except Exception as e:  # NWS has occasional 500s; retry briefly
            last = e
            time.sleep(1 + attempt)
    raise RuntimeError(f"weather.gov failed: {url}: {last}")


class WeatherData:
    """Caches per-station lookups for the duration of one scan (and gridpoints across scans)."""

    def __init__(self, broker):
        self.db = broker.db
        self.grid = {}
        self.obs = {}

    def _point(self, code):
        st, lat, lon = STATIONS[code]
        key = f"nws:{code}"
        row = self.db.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        if row:
            return json.loads(row[0])
        p = _nws(f"/points/{lat:.4f},{lon:.4f}")["properties"]
        info = {"grid": p["forecastGridData"], "tz": p["timeZone"]}
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(info)))
        self.db.commit()
        return info

    def tz(self, code):
        return ZoneInfo(self._point(code)["tz"])

    def forecast_highs(self, code):
        """{local date: forecast high °F} from the official NWS forecast."""
        if code not in self.grid:
            info = self._point(code)
            tz = ZoneInfo(info["tz"])
            vals = _nws(info["grid"])["properties"]["maxTemperature"]["values"]
            out = {}
            for v in vals:
                if v.get("value") is None:
                    continue
                start = datetime.fromisoformat(v["validTime"].split("/")[0])
                out[start.astimezone(tz).date()] = c_to_f(v["value"])
            self.grid[code] = out
        return self.grid[code]

    def model_highs(self, code):
        """{local date: [high °F from each forecast model]} via Open-Meteo (free, no key)."""
        k = ("models", code)
        if k not in self.grid:
            st, lat, lon = STATIONS[code]
            q = (f"{OPEN_METEO}?latitude={lat:.4f}&longitude={lon:.4f}&daily=temperature_2m_max"
                 f"&temperature_unit=fahrenheit&timezone={self._point(code)['tz'].replace('/', '%2F')}"
                 f"&forecast_days=3&models={','.join(MODELS)}")
            with urllib.request.urlopen(urllib.request.Request(q, headers={"User-Agent": UA["User-Agent"]}), timeout=20) as r:
                d = json.load(r)["daily"]
            out = {}
            for i, t in enumerate(d["time"]):
                day = datetime.fromisoformat(t).date()
                out[day] = [d.get(f"temperature_2m_max_{m}", [None] * (i + 1))[i] for m in MODELS]
            self.grid[k] = out
        return self.grid[k]

    def observed_max(self, code, day):
        """Highest temperature (°F) the station has recorded so far in that climate day, or None."""
        k = (code, day)
        if k not in self.obs:
            st = STATIONS[code][0]
            start, end = climate_day_utc(day, self.tz(code))
            data = _nws(f"/stations/{st}/observations?start={start.strftime('%Y-%m-%dT%H:%M:%SZ')}&limit=500")
            temps = []
            for f in data.get("features", []):
                p = f["properties"]
                t = datetime.fromisoformat(p["timestamp"])
                v = (p.get("temperature") or {}).get("value")
                if v is not None and start <= t < end:
                    temps.append(c_to_f(v))
            self.obs[k] = max(temps) if temps else None
        return self.obs[k]


# ---------------- strategy ----------------

def weather_series(source):
    """Kalshi series tickers for US daily-high markets (found by name, so new cities appear automatically)."""
    out = []
    for s in source.list_series("Climate and Weather"):
        title = (s.get("title") or "").lower()
        if s.get("frequency") == "daily" and "temp" in title and ("high" in title or "max" in title):
            out.append(s["ticker"])
    return out


def fetch_markets(source):
    markets = []
    for t in weather_series(source):
        try:
            markets += source.list_series_markets(t)
        except Exception as e:
            print(f"  ! weather series {t}: {e}")
    return markets


def signals(markets, now, broker, wx=None):
    c = config.WEATHER
    if not c["enabled"]:
        return []
    wx = wx or WeatherData(broker)
    out, skipped = [], set()
    for m in markets:
        raw = m.raw or {}
        code = site_code(raw.get("rules_primary"))
        day = parse_event_date(m.event_ticker)
        if code not in STATIONS or day is None:
            if code:
                skipped.add(code)
            continue
        try:
            tz = wx.tz(code)
            now_local = now.astimezone(tz)
            ahead = (day - now_local.date()).days
            if ahead < 0 or ahead > c["max_days_ahead"]:
                continue
            fc = wx.forecast_highs(code).get(day)
            obs = wx.observed_max(code, day) if ahead == 0 else None
        except Exception as e:
            print(f"  ! weather data {code}: {e}")
            continue
        if fc is None and obs is None:
            continue
        if fc is None:
            fc = obs
        try:
            sp = spread(wx.model_highs(code).get(day, []))
        except Exception as e:
            print(f"  ! forecast models {code}: {e}")
            sp = None
        mu, sigma, floor = model(fc, now_local, day, obs, c, sp)
        st = raw.get("strike_type")
        lo, hi = raw.get("floor_strike"), raw.get("cap_strike")
        try:
            p = bracket_prob(st, lo, hi, mu, sigma, floor)
        except (ValueError, TypeError):
            continue
        p = min(max(p, 0.005), 0.995)
        best = None
        for side, prob, price in (("yes", p, m.yes_ask), ("no", 1 - p, m.no_ask)):
            if price is None or not (c["price_min"] <= price <= c["price_max"]):
                continue
            edge = prob - price - fee_per_contract(price)
            if edge >= c["min_edge"] and (best is None or edge > best[3]):
                best = (side, prob, price, edge)
        if best:
            side, prob, price, edge = best
            why = (f"{code} fcst {fc:.0f}°F ±{sigma:.1f}" + (f", seen {obs:.0f}°F" if obs is not None else "")
                   + f" -> YES {p*100:.0f}% vs {m.yes_ask*100 if m.yes_ask else 0:.0f}c")
            out.append(Signal(m, side, price, prob, edge, "weather", why))
    if skipped:
        print(f"  (weather: no station mapping for {', '.join(sorted(skipped))})")
    return out
