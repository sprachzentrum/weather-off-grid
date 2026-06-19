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

from datetime import datetime

# Pressure window the Zambretti mapping is calibrated for.
Z_BOTTOM = 950.0
Z_TOP = 1050.0
Z_CONSTANT = (Z_TOP - Z_BOTTOM) / 22.0  # 4.5454...

# |Δp| over 3 h below this is "steady".
TREND_STEADY_HPA = 1.0
# Forecast index at/above which meaningful rain is implied (for the OM compare).
RAIN_INDEX = 13

# Beteljuice option tables: map the pressure step (0=low .. 21=high) to a
# forecast index 0..25. Value 0 = best ("Settled fine"), 25 = worst ("Stormy").
_RISE = [25, 25, 25, 24, 24, 19, 16, 12, 11, 9, 8, 6, 5, 2, 1, 1, 0, 0, 0, 0, 0, 0]
_STEADY = [25, 25, 25, 25, 25, 25, 23, 23, 22, 18, 15, 13, 10, 4, 1, 1, 0, 0, 0, 0, 0, 0]
_FALL = [25, 25, 25, 25, 25, 25, 25, 25, 23, 23, 21, 20, 17, 14, 7, 3, 1, 1, 1, 0, 0, 0]

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


def trend_3h(times: list[str], values: list) -> float | None:
    """
    Pressure change (hPa) over ~3 h: latest value minus the reading closest to
    3 h earlier. Returns None if there is not enough data.
    """
    pts = [
        (datetime.fromisoformat(t.replace("Z", "+00:00")), v)
        for t, v in zip(times or [], values or [])
        if v is not None and t
    ]
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


def _wind_pressure_offset(wind_deg: float | None, southern: bool) -> float:
    """
    Small Zambretti wind correction (hPa), applied to the effective pressure.
    Northern-hemisphere convention: northerly/easterly winds raise (improve),
    south-westerly lower (worsen). Southern hemisphere rotates the wind 180°.
    """
    if wind_deg is None:
        return 0.0
    deg = (wind_deg + 180) % 360 if southern else wind_deg % 360
    # 8-point offset, N..NW.
    offsets = [5.0, 3.0, 2.0, 0.0, -2.0, -5.0, -3.0, 1.0]
    sector = int(deg / 45.0 + 0.5) % 8
    return offsets[sector]


def zambretti(
    pressure: float | None,
    trend: str,
    wind_deg: float | None,
    month: int,
    southern: bool = True,
) -> dict | None:
    """
    Compute the Zambretti forecast.

    pressure: relative (sea-level) pressure in hPa.
    trend: "rising" | "steady" | "falling".
    wind_deg: wind direction in degrees (or None to skip the wind correction).
    month: 1-12 (used for the S-hemisphere season swap).
    southern: True for the southern hemisphere.
    """
    if pressure is None:
        return None

    # Effective pressure after the wind correction, clamped to the dial range.
    p_eff = pressure + _wind_pressure_offset(wind_deg, southern)
    p_eff = max(Z_BOTTOM, min(Z_TOP, p_eff))

    # Pressure step 0 (low) .. 21 (high).
    step = round((p_eff - Z_BOTTOM) / Z_CONSTANT)
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
    }
