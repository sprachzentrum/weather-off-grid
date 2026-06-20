"""
Forest Fire Danger Index (McArthur FFDI, simplified) from local sensor data.

Córdoba's Sierras burn regularly in the dry winter/spring; a local fire-danger
index built from the station's own live readings is genuinely safety relevant,
because the regional models miss the dry, gusty microclimate of a single valley.

We use a simplified McArthur Mk5 Forest Fire Danger Index:

    DF   = min(10, days_since_rain * 0.5)            # 0..10 drought factor
    FFDI = 2 * exp(-0.45 + 0.987*ln(DF + 0.001)
                   - 0.0345*RH + 0.0338*T + 0.0234*V)

with T = temperature (°C), RH = relative humidity (%), V = wind speed (km/h).
The drought factor is derived from the days since the last significant rain
(> 2 mm), read from the local rain history in InfluxDB.

The module exposes small pure helpers (`compute_ffdi`, `categorise`,
`drought_factor`) so both the /api/fire-danger endpoint and the PDF report can
reuse exactly the same maths.
"""
from __future__ import annotations

import logging
import math

import config
import db
import settings_store

log = logging.getLogger("fire_danger")

# Rain above this (mm, daily total) "resets" the drought clock.
SIGNIFICANT_RAIN_MM = 2.0
# How far back to look for the last significant rain.
RAIN_LOOKBACK_DAYS = 90

# Category bands keyed by FFDI. (lower_inclusive, upper_exclusive, key, color,
# emoji, {lang: label}). The top band is open-ended.
CATEGORIES = [
    (0.0, 5.0, "low", "green", "🟢",
     {"de": "Niedrig", "en": "Low", "es": "Bajo"}),
    (5.0, 12.0, "moderate", "yellow", "🟡",
     {"de": "Mäßig", "en": "Moderate", "es": "Moderado"}),
    (12.0, 24.0, "high", "orange", "🟠",
     {"de": "Hoch", "en": "High", "es": "Alto"}),
    (24.0, 50.0, "very_high", "red", "🔴",
     {"de": "Sehr hoch", "en": "Very high", "es": "Muy alto"}),
    (50.0, float("inf"), "extreme", "darkred", "⚫",
     {"de": "Extrem", "en": "Extreme", "es": "Extremo"}),
]


def drought_factor(days_since_rain: float) -> float:
    """Simplified drought factor on a 0..10 scale from days since rain > 2 mm."""
    return max(0.0, min(10.0, days_since_rain * 0.5))


def compute_ffdi(temperature: float, humidity: float, wind_speed: float,
                 df: float) -> float:
    """
    McArthur FFDI from temperature (°C), humidity (%), wind (km/h) and the
    drought factor `df` (0..10). Returns a non-negative index rounded to 1 dp.
    """
    exponent = (
        -0.45
        + 0.987 * math.log(df + 0.001)
        - 0.0345 * humidity
        + 0.0338 * temperature
        + 0.0234 * wind_speed
    )
    return round(max(0.0, 2.0 * math.exp(exponent)), 1)


def categorise(ffdi: float, lang: str = "de") -> dict:
    """Map an FFDI value to its category, colour, emoji and localised label."""
    for low, high, key, color, emoji, labels in CATEGORIES:
        if low <= ffdi < high:
            return {
                "category": key,
                "color": color,
                "emoji": emoji,
                "label": labels.get(lang, labels["de"]),
                "label_de": labels["de"],
                "label_en": labels["en"],
                "label_es": labels["es"],
            }
    # ffdi below 0 should be impossible (clamped), but be safe.
    low, high, key, color, emoji, labels = CATEGORIES[0]
    return {
        "category": key, "color": color, "emoji": emoji,
        "label": labels.get(lang, labels["de"]),
        "label_de": labels["de"], "label_en": labels["en"], "label_es": labels["es"],
    }


# ── InfluxDB: daily rain totals + days since significant rain ───────────────
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


def daily_rain(site_id: str | None, days: int = RAIN_LOOKBACK_DAYS) -> dict[str, float]:
    """
    Per-day rain total (mm) -> {date_iso: mm}. `rain_daily` is a cumulative
    counter that resets at local midnight, so its daily MAX is the day's total
    (same approach the microclimate model uses).
    """
    flux = f'''
    from(bucket: "{config.BUCKET_WEATHER}")
      |> range(start: -{days}d)
      |> filter(fn: (r) => r._measurement == "station" and r._field == "rain_daily"{_site_line(site_id)})
      |> aggregateWindow(every: 1d, fn: max, timeSrc: "_start", createEmpty: false)
    '''
    out: dict[str, float] = {}
    try:
        for table in db.query(flux):
            for rec in table.records:
                v = rec.get_value()
                if v is not None:
                    out[rec.get_time().date().isoformat()] = float(v)
    except Exception as exc:  # noqa: BLE001
        log.debug("daily_rain query failed: %s", exc)
    return out


def days_since_rain(site_id: str | None, today_iso: str | None = None) -> int:
    """
    Whole days since the last day with > SIGNIFICANT_RAIN_MM of rain.

    Counts back from today over the daily rain history; 0 means it rained
    significantly today. If no rain is found in the lookback window, returns the
    window length (a long dry spell) so the drought factor saturates at 10.
    """
    from datetime import date

    rain = daily_rain(site_id)
    today = date.fromisoformat(today_iso) if today_iso else date.today()
    for back in range(0, RAIN_LOOKBACK_DAYS + 1):
        day = (today.toordinal() - back)
        iso = date.fromordinal(day).isoformat()
        if rain.get(iso, 0.0) > SIGNIFICANT_RAIN_MM:
            return back
    return RAIN_LOOKBACK_DAYS


def longest_dry_spell(rain: dict[str, float]) -> int:
    """
    Longest run of consecutive days with rain <= SIGNIFICANT_RAIN_MM in a daily
    rain map. Used by the PDF report's fire-risk summary. Days with no data are
    treated as dry (no measured rain).
    """
    if not rain:
        return 0
    from datetime import date

    days = sorted(rain)
    start = date.fromisoformat(days[0])
    end = date.fromisoformat(days[-1])
    longest = run = 0
    d = start
    while d <= end:
        wet = rain.get(d.isoformat(), 0.0) > SIGNIFICANT_RAIN_MM
        run = 0 if wet else run + 1
        longest = max(longest, run)
        d = date.fromordinal(d.toordinal() + 1)
    return longest
