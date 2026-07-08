"""
Barometric short-term forecast from local sensor data (Zambretti).

Uses only the local Ecowitt station's relative (sea-level) pressure, the 3-hour
pressure trend and the wind direction to produce a 6-12 h outlook. Works fully
offline and is often more accurate than regional models in mountain valleys.

Reference: https://en.wikipedia.org/wiki/Zambretti_Forecaster
(Beteljuice digitisation of the Negretti & Zambra slide-rule forecaster.)

Southern-hemisphere adaptation: the wind direction is rotated 180° and the
season is shifted by 6 months, per the standard Zambretti S-hemisphere rule.

The forecast is returned as an index 0..25 (A..Z, best..worst) plus severity so
the frontend can localise the text itself; a German text is included as a
fallback for non-localising consumers (e.g. the iOS widget).
"""
from __future__ import annotations

import math
from datetime import datetime

# Pressure window the Zambretti mapping is calibrated for.
Z_BOTTOM = 950.0
Z_TOP = 1050.0
Z_RANGE = Z_TOP - Z_BOTTOM
Z_CONSTANT = Z_RANGE / 22.0  # 4.5454...

# |Δp| over 3 h below this is "steady".
TREND_STEADY_HPA = 1.0
# Below this wind speed (km/h) the vane direction is noise: skip the wind correction.
CALM_WIND_KMH = 2.0
# Beteljuice seasonal correction: in local summer a rising barometer reads
# +7 % of the dial range, a falling one -7 %.
SEASON_OFFSET_HPA = 7.0 / 100.0 * Z_RANGE
# Forecast index at/above which meaningful rain is implied (for the OM compare).
RAIN_INDEX = 13

# Beteljuice option tables: map the pressure step (0=low .. 21=high) to a
# forecast index 0..25. Value 0 = best ("Settled fine"), 25 = worst ("Stormy").
_RISE = [25, 25, 25, 24, 24, 19, 16, 12, 11, 9, 8, 6, 5, 2, 1, 1, 0, 0, 0, 0, 0, 0]
_STEADY = [25, 25, 25, 25, 25, 25, 23, 23, 22, 18, 15, 13, 10, 4, 1, 1, 0, 0, 0, 0, 0, 0]
_FALL = [25, 25, 25, 25, 25, 25, 25, 25, 23, 23, 21, 20, 17, 14, 7, 3, 1, 1, 1, 0, 0, 0]

# Beteljuice wind correction, 16 sectors N..NNW, in % of the dial range
# (equals hPa for the standard 100-hPa dial). Northerly/easterly winds raise
# the effective pressure (improve), southerly lower it (worsen).
_WIND_OFFSETS_PCT = [
    6.0,    # N
    5.0,    # NNE
    5.0,    # NE
    2.0,    # ENE
    -0.5,   # E
    -2.0,   # ESE
    -5.0,   # SE
    -8.5,   # SSE
    -12.0,  # S
    -10.0,  # SSW
    -6.0,   # SW
    -4.5,   # WSW
    -3.0,   # W
    -0.5,   # WNW
    1.5,    # NW
    3.0,    # NNW
]

# German fallback texts, index 0 (best) .. 25 (worst). The frontend has the
# localised arrays keyed by the same index.
FORECAST_DE = [
    "Beständig schön", "Schönes Wetter", "Wird zunehmend schön",
    "Schön, aber wechselhafter", "Schön, einzelne Schauer möglich",
    "Recht schön, Besserung", "Recht schön, früh evtl. Schauer",
    "Recht schön, später Schauer", "Früh Schauer, dann Besserung",
    "Wechselhaft, Besserung", "Recht schön, Schauer wahrscheinlich",
    "Eher unbeständig, später Aufklaren", "Unbeständig, wahrscheinlich Besserung",
    "Schauer, sonnige Abschnitte", "Schauer, zunehmend unbeständig",
    "Wechselhaft, etwas Regen", "Unbeständig, kurze schöne Phasen",
    "Unbeständig, später Regen", "Unbeständig, etwas Regen",
    "Überwiegend sehr unbeständig", "Zeitweise Regen, Verschlechterung",
    "Zeitweise Regen, sehr unbeständig", "Häufig Regen", "Regen, sehr unbeständig",
    "Stürmisch, viel Regen", "Stürmisch, sehr nass",
]

_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def classify_trend(delta_hpa: float | None) -> tuple[str, str]:
    """(trend, arrow) from the 3-hour pressure change in hPa."""
    if delta_hpa is None:
        return "steady", "→"
    if delta_hpa >= TREND_STEADY_HPA:
        return "rising", "↑"
    if delta_hpa <= -TREND_STEADY_HPA:
        return "falling", "↓"
    return "steady", "→"


def trend_3h(
    times: list[str],
    values: list,
    anchor_time: datetime | None = None,
    anchor_value: float | None = None,
) -> float | None:
    """
    Pressure change (hPa) over ~3 h: latest value minus the reading closest to
    3 h earlier. Returns None if there is not enough data.

    anchor_time/anchor_value: the station's freshest live reading. The series
    is window-averaged (its last point lags and is a partial-window mean), so
    when given, the live reading is used as the "now" end of the delta.
    """
    pts = [
        (datetime.fromisoformat(t.replace("Z", "+00:00")), v)
        for t, v in zip(times or [], values or [])
        if v is not None and t
    ]
    if anchor_time is not None and anchor_value is not None and anchor_time.tzinfo:
        pts.append((anchor_time, float(anchor_value)))
    if len(pts) < 2:
        return None
    pts.sort(key=lambda p: p[0])
    last_t, last_v = pts[-1]
    target = last_t.timestamp() - 3 * 3600
    # Reading nearest to 3 h ago, but at least ~1 h back so the delta is meaningful.
    older = [(t, v) for t, v in pts[:-1] if last_t.timestamp() - t.timestamp() >= 3600]
    if not older:
        return None
    ref_t, ref_v = min(older, key=lambda p: abs(p[0].timestamp() - target))
    return round(last_v - ref_v, 1)


def _severity(index: int) -> tuple[str, str]:
    """(severity, color) for a forecast index."""
    if index <= 7:
        return "good", "green"
    if index <= 15:
        return "changeable", "yellow"
    return "deteriorating", "red"


def _is_local_summer(month: int, southern: bool) -> bool:
    """Zambretti season: Apr-Sep is northern summer; inverted down south."""
    northern_summer = 4 <= month <= 9
    return not northern_summer if southern else northern_summer


def _wind_pressure_offset(
    wind_deg: float | None, southern: bool, wind_speed: float | None = None
) -> float:
    """
    Zambretti wind correction (hPa) applied to the effective pressure, using
    the 16-sector Beteljuice table. Southern hemisphere rotates the wind 180°.
    Skipped in calm conditions, where the vane direction carries no signal.
    """
    if wind_deg is None:
        return 0.0
    if wind_speed is not None and wind_speed < CALM_WIND_KMH:
        return 0.0
    deg = (wind_deg + 180) % 360 if southern else wind_deg % 360
    sector = int(deg / 22.5 + 0.5) % 16
    return _WIND_OFFSETS_PCT[sector] / 100.0 * Z_RANGE


def zambretti(
    pressure: float | None,
    trend: str,
    wind_deg: float | None,
    month: int,
    southern: bool = True,
    wind_speed: float | None = None,
) -> dict | None:
    """
    Compute the Zambretti forecast.

    pressure: relative (sea-level) pressure in hPa.
    trend: "rising" | "steady" | "falling".
    wind_deg: wind direction in degrees (or None to skip the wind correction).
    month: 1-12 (drives the summer/winter correction, season-swapped down south).
    southern: True for the southern hemisphere.
    wind_speed: km/h; below CALM_WIND_KMH the wind correction is skipped.
    """
    if pressure is None:
        return None

    # Effective pressure after the wind and season corrections.
    p_eff = pressure + _wind_pressure_offset(wind_deg, southern, wind_speed)
    summer = _is_local_summer(month, southern)
    if summer:
        if trend == "rising":
            p_eff += SEASON_OFFSET_HPA
        elif trend == "falling":
            p_eff -= SEASON_OFFSET_HPA
    p_eff = max(Z_BOTTOM, min(Z_TOP, p_eff))

    # Pressure step 0 (low) .. 21 (high); floor per the Beteljuice reference.
    step = math.floor((p_eff - Z_BOTTOM) / Z_CONSTANT)
    step = max(0, min(21, step))

    table = {"rising": _RISE, "steady": _STEADY, "falling": _FALL}.get(trend, _STEADY)
    index = table[step]
    severity, color = _severity(index)

    return {
        "index": index,
        "letter": _LETTERS[index],
        "text": FORECAST_DE[index],
        "severity": severity,
        "color": color,
        "rain_likely": index >= RAIN_INDEX,
        "pressure_effective": round(p_eff, 1),
        "season_summer": summer,
    }
