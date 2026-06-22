"""
Frost & fruit-growing metrics from local sensor + forecast data.

El Durazno ("the peach") sits at altitude in the Córdoba sierras where radiative
night frosts are the main hazard for fruit trees - and the regional forecast,
read at 2 m, routinely understates ground frost in a single cold valley. This
module turns the data we already collect into the numbers a grower actually
watches:

  * Frost forecast - the coming night's expected low (from the hourly Open-Meteo
    forecast), a frost category, the hour frost is expected to set in, and the
    next frost night in the 7-day outlook.
  * Chill hours    - hours in the 0..7.2 °C band, accumulated from the station
    history. The classic dormancy/bud-break model for stone fruit.
  * Growing degree days (GDD) - heat accumulation above a base temperature
    (default 10 °C for peach), from the station's daily max/min.

Pure helpers (`categorise_low`, `dew_point`) are kept separate from the InfluxDB
readers so the API endpoint and the PDF report can share the same maths.
"""
from __future__ import annotations

import logging
import math

import config
import db
import settings_store

log = logging.getLogger("frost")

# Air temperature (°C) at/below which we flag frost. Note that radiative ground
# frost can occur with the 2 m air temperature still a few degrees above zero,
# which is why the 0..3 °C band is its own "ground frost risk" category.
FROST_THRESHOLD = 0.0

# Chill-hour model: hours with temperature in [CHILL_LOW, CHILL_HIGH].
CHILL_LOW = 0.0
CHILL_HIGH = 7.2  # 45 °F - the classic chill-hour ceiling

# Growing degree days base temperature (°C). 10 °C is a common base for peach.
GDD_BASE = 10.0

# Overnight-low categories, evaluated high → low. (upper_exclusive, key, color,
# emoji, {lang: label}); the coldest band is open-ended (-inf).
LOW_CATEGORIES = [
    (float("inf"), 3.0, "none", "green", "🟢",
     {"de": "Kein Frost", "en": "No frost", "es": "Sin helada"}),
    (3.0, 0.0, "ground", "yellow", "🟡",
     {"de": "Bodenfrost möglich", "en": "Ground frost possible", "es": "Posible helada de suelo"}),
    (0.0, -2.0, "light", "orange", "🟠",
     {"de": "Leichter Frost", "en": "Light frost", "es": "Helada leve"}),
    (-2.0, -5.0, "moderate", "red", "🔴",
     {"de": "Mäßiger Frost", "en": "Moderate frost", "es": "Helada moderada"}),
    (-5.0, float("-inf"), "severe", "darkred", "⚫",
     {"de": "Strenger Frost", "en": "Severe frost", "es": "Helada severa"}),
]


def categorise_low(min_temp: float | None, lang: str = "de") -> dict:
    """Map an overnight-low (°C) to its frost category, colour, emoji and label."""
    if min_temp is None:
        labels = {"de": "Keine Daten", "en": "No data", "es": "Sin datos"}
        return {"category": "unknown", "color": "gray", "emoji": "⚪",
                "label": labels.get(lang, labels["de"]),
                "label_de": labels["de"], "label_en": labels["en"], "label_es": labels["es"]}
    for high, low, key, color, emoji, labels in LOW_CATEGORIES:
        if low <= min_temp < high:
            return {"category": key, "color": color, "emoji": emoji,
                    "label": labels.get(lang, labels["de"]),
                    "label_de": labels["de"], "label_en": labels["en"], "label_es": labels["es"]}
    high, low, key, color, emoji, labels = LOW_CATEGORIES[-1]
    return {"category": key, "color": color, "emoji": emoji,
            "label": labels.get(lang, labels["de"]),
            "label_de": labels["de"], "label_en": labels["en"], "label_es": labels["es"]}


def is_frost(min_temp: float | None) -> bool:
    return min_temp is not None and min_temp <= FROST_THRESHOLD


def dew_point(temperature: float, humidity: float) -> float | None:
    """Dew/frost point (°C) via the Magnus formula. Below 0 °C this is the frost
    point - the temperature the air must cool to before frost deposits."""
    if humidity is None or humidity <= 0:
        return None
    a, b = 17.27, 237.7
    gamma = (a * temperature) / (b + temperature) + math.log(humidity / 100.0)
    try:
        return round(b * gamma / (a - gamma), 1)
    except ZeroDivisionError:
        return None


# ── InfluxDB readers ────────────────────────────────────────────────────────
def _site_line(site_id: str | None) -> str:
    """Site restriction; the default site also claims untagged historical points."""
    if not site_id:
        return ""
    try:
        is_default = site_id == settings_store.default_site_id()
    except Exception:  # noqa: BLE001
        is_default = False
    if is_default:
        return f' and (r.site_id == "{site_id}" or not exists r.site_id)'
    return f' and r.site_id == "{site_id}"'


def chill_hours(site_id: str | None, days: int = 60) -> int | None:
    """Hours in the [CHILL_LOW, CHILL_HIGH] band over the last `days`, from the
    station's outdoor temperature (hourly mean). None if there is no data."""
    flux = f'''
    from(bucket: "{config.BUCKET_WEATHER}")
      |> range(start: -{days}d)
      |> filter(fn: (r) => r._measurement == "station" and r._field == "temperature_outdoor"{_site_line(site_id)})
      |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
    '''
    try:
        hours = 0
        seen = False
        for table in db.query(flux):
            for rec in table.records:
                v = rec.get_value()
                if v is None:
                    continue
                seen = True
                if CHILL_LOW <= float(v) <= CHILL_HIGH:
                    hours += 1
        return hours if seen else None
    except Exception as exc:  # noqa: BLE001
        log.debug("chill_hours query failed: %s", exc)
        return None


def _daily_extreme(site_id: str | None, days: int, fn: str) -> dict[str, float]:
    flux = f'''
    from(bucket: "{config.BUCKET_WEATHER}")
      |> range(start: -{days}d)
      |> filter(fn: (r) => r._measurement == "station" and r._field == "temperature_outdoor"{_site_line(site_id)})
      |> aggregateWindow(every: 1d, fn: {fn}, timeSrc: "_start", createEmpty: false)
    '''
    out: dict[str, float] = {}
    try:
        for table in db.query(flux):
            for rec in table.records:
                v = rec.get_value()
                if v is not None:
                    out[rec.get_time().date().isoformat()] = float(v)
    except Exception as exc:  # noqa: BLE001
        log.debug("daily extreme (%s) query failed: %s", fn, exc)
    return out


def growing_degree_days(site_id: str | None, days: int = 30,
                        base: float = GDD_BASE) -> float | None:
    """Accumulated GDD over the last `days`: sum of max(0, (Tmax+Tmin)/2 - base)
    per day, from the station's daily temperature extremes. None if no data."""
    tmax = _daily_extreme(site_id, days, "max")
    tmin = _daily_extreme(site_id, days, "min")
    common = set(tmax) & set(tmin)
    if not common:
        return None
    total = 0.0
    for day in common:
        mean = (tmax[day] + tmin[day]) / 2.0
        total += max(0.0, mean - base)
    return round(total, 1)


# Southern-hemisphere season halves used to pin down the frost season: a late
# (spring) frost ends the cold season, an early (autumn) frost starts it. These
# are flipped for the northern hemisphere.
_SPRING_MONTHS_S = {8, 9, 10, 11}
_AUTUMN_MONTHS_S = {3, 4, 5, 6}


def season_stats(site_id: str | None, days: int = 400,
                 hemisphere: str = "south") -> dict | None:
    """
    Climatology from the station's own history, to sanity-check when the garden
    season really starts here. Returns, per calendar month, the mean daily
    min/max and the number of observed frost nights (min ≤ 0 °C), plus the
    latest spring frost and earliest autumn frost actually recorded. None if
    there is no usable history yet.
    """
    tmin = _daily_extreme(site_id, days, "min")
    tmax = _daily_extreme(site_id, days, "max")
    if not tmin:
        return None

    from collections import defaultdict
    sum_min: dict[int, float] = defaultdict(float)
    cnt_min: dict[int, int] = defaultdict(int)
    sum_max: dict[int, float] = defaultdict(float)
    cnt_max: dict[int, int] = defaultdict(int)
    frost_by_month: dict[int, int] = defaultdict(int)
    frost_dates: list[str] = []

    for day, v in tmin.items():
        m = int(day[5:7])
        sum_min[m] += v
        cnt_min[m] += 1
        if v <= FROST_THRESHOLD:
            frost_by_month[m] += 1
            frost_dates.append(day)
    for day, v in tmax.items():
        m = int(day[5:7])
        sum_max[m] += v
        cnt_max[m] += 1

    months = [{
        "month": m,
        "mean_min": round(sum_min[m] / cnt_min[m], 1) if cnt_min[m] else None,
        "mean_max": round(sum_max[m] / cnt_max[m], 1) if cnt_max[m] else None,
        "frost_days": frost_by_month[m],
        "observed_days": cnt_min[m],
    } for m in range(1, 13)]

    spring = _SPRING_MONTHS_S if hemisphere == "south" else {2, 3, 4, 5}
    autumn = _AUTUMN_MONTHS_S if hemisphere == "south" else {9, 10, 11, 12}
    spring_frosts = [d for d in frost_dates if int(d[5:7]) in spring]
    autumn_frosts = [d for d in frost_dates if int(d[5:7]) in autumn]

    return {
        "window_days": days,
        "months": months,
        "frost_day_total": len(frost_dates),
        "last_spring_frost": max(spring_frosts) if spring_frosts else None,
        "first_autumn_frost": min(autumn_frosts) if autumn_frosts else None,
    }
