"""
Microclimate correction model.

A lightweight statistical model (no ML framework) that learns how the local
station systematically deviates from the Open-Meteo regional forecast, by pairing
each day's 1-day-ahead archived forecast (bucket `forecasts`, lead_days=1) with
the actually measured weather (bucket `weather`).

It produces three correction families:
  1. Temperature bias per calendar month (measured - forecast, for tmax/tmin).
  2. Conditional rain probability (did it actually rain when rain was forecast?),
     also broken down by dominant wind direction.
  3. Wind scaling per direction (measured_max / forecast_max).

Nothing is applied until >= MIN_DAYS of paired data exist; a confidence score
scales from there up to FULL_CONFIDENCE_DAYS. Results are cached in-process.
"""
from __future__ import annotations

import logging
import math
import time
from collections import defaultdict

import config
import db

log = logging.getLogger("microclimate")

MIN_DAYS = 30            # do not correct anything below this
FULL_CONFIDENCE_DAYS = 90
LOOKBACK_DAYS = 400      # ~13 months so every calendar month can contribute
RAIN_THRESHOLD_MM = 1.0  # measured rain above this counts as "it rained"
_CACHE_TTL = 3600        # rebuild model at most hourly

_cache: dict[str, tuple[float, dict]] = {}

SECTORS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _sector(deg) -> str | None:
    if not isinstance(deg, (int, float)):
        return None
    return SECTORS[int((deg % 360) / 45.0 + 0.5) % 8]


# ── InfluxDB readers ───────────────────────────────────────────────────────
def _daily_agg(field: str, fn: str) -> dict[str, float]:
    """Per-day aggregate of a measured weather field -> {date: value}."""
    flux = f'''
    from(bucket: "{config.BUCKET_WEATHER}")
      |> range(start: -{LOOKBACK_DAYS}d)
      |> filter(fn: (r) => r._measurement == "station" and r._field == "{field}")
      |> aggregateWindow(every: 1d, fn: {fn}, createEmpty: false)
    '''
    out: dict[str, float] = {}
    try:
        for table in db.query(flux):
            for rec in table.records:
                value = rec.get_value()
                if value is not None:
                    out[rec.get_time().date().isoformat()] = float(value)
    except Exception as exc:  # noqa: BLE001
        log.debug("measured agg %s/%s failed: %s", field, fn, exc)
    return out


def _measured_daily() -> dict[str, dict]:
    tmax = _daily_agg("temperature_outdoor", "max")
    tmin = _daily_agg("temperature_outdoor", "min")
    rain = _daily_agg("rain_daily", "max")  # cumulative counter -> day total
    wind = _daily_agg("wind_speed", "max")
    wdir = _daily_agg("wind_direction", "mean")
    days = set(tmax) | set(tmin) | set(rain) | set(wind)
    return {
        d: {
            "tmax": tmax.get(d),
            "tmin": tmin.get(d),
            "rain": rain.get(d),
            "wind_max": wind.get(d),
            "wind_dir": wdir.get(d),
        }
        for d in days
    }


def _forecast_daily() -> dict[str, dict]:
    """1-day-ahead archived forecasts keyed by their target date."""
    flux = f'''
    from(bucket: "{config.BUCKET_FORECASTS}")
      |> range(start: -{LOOKBACK_DAYS}d)
      |> filter(fn: (r) => r._measurement == "forecast_daily" and r.lead_days == "1")
      |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    out: dict[str, dict] = {}
    try:
        for table in db.query(flux):
            for rec in table.records:
                target = rec.values.get("target_date") or rec.get_time().date().isoformat()
                out[target] = {
                    "tmax": rec.values.get("temp_max"),
                    "tmin": rec.values.get("temp_min"),
                    "precip_prob": rec.values.get("precip_prob"),
                    "precip_sum": rec.values.get("precip_sum"),
                    "wind_max": rec.values.get("wind_max"),
                    "wind_dir": rec.values.get("wind_dir"),
                }
    except Exception as exc:  # noqa: BLE001
        log.debug("forecast read failed: %s", exc)
    return out


# ── Model building ─────────────────────────────────────────────────────────
def _build() -> dict:
    measured = _measured_daily()
    forecast = _forecast_daily()
    pairs = []
    for day, m in measured.items():
        f = forecast.get(day)
        if f:
            pairs.append((day, f, m))

    n = len(pairs)
    if n < MIN_DAYS:
        return {"days_of_data": n, "active": False, "confidence": 0.0}

    confidence = min(1.0, n / FULL_CONFIDENCE_DAYS)

    # 1) Temperature bias per month -------------------------------------------
    month_tmax: dict[int, list[float]] = defaultdict(list)
    month_tmin: dict[int, list[float]] = defaultdict(list)
    all_tmax_diff: list[float] = []
    for day, f, m in pairs:
        month = int(day[5:7])
        if _num(f.get("tmax")) is not None and _num(m.get("tmax")) is not None:
            d = m["tmax"] - f["tmax"]
            month_tmax[month].append(d)
            all_tmax_diff.append(d)
        if _num(f.get("tmin")) is not None and _num(m.get("tmin")) is not None:
            month_tmin[month].append(m["tmin"] - f["tmin"])

    temp_bias = {
        str(mo): {
            "tmax": round(_avg(month_tmax.get(mo, [])), 2),
            "tmin": round(_avg(month_tmin.get(mo, [])), 2),
        }
        for mo in range(1, 13)
        if month_tmax.get(mo) or month_tmin.get(mo)
    }
    temp_bias_overall = {
        "tmax": round(_avg(all_tmax_diff), 2),
        "tmin": round(_avg([x for lst in month_tmin.values() for x in lst]), 2),
    }

    # 2) Conditional rain probability -----------------------------------------
    fc_rain = fc_rain_hit = no_fc = no_fc_rain = 0
    dir_hits: dict[str, list[int]] = defaultdict(list)
    for day, f, m in pairs:
        prob = _num(f.get("precip_prob"))
        measured_rain = (_num(m.get("rain")) or 0) >= RAIN_THRESHOLD_MM
        if prob is None:
            continue
        forecast_rain = prob >= 50
        if forecast_rain:
            fc_rain += 1
            fc_rain_hit += int(measured_rain)
            sec = _sector(m.get("wind_dir"))
            if sec:
                dir_hits[sec].append(int(measured_rain))
        else:
            no_fc += 1
            no_fc_rain += int(measured_rain)

    rain = {
        "p_rain_given_forecast": round(fc_rain_hit / fc_rain, 3) if fc_rain else None,
        "p_rain_given_no_forecast": round(no_fc_rain / no_fc, 3) if no_fc else None,
        "by_dir": {
            sec: round(sum(hits) / len(hits), 3) for sec, hits in dir_hits.items() if hits
        },
    }

    # 3) Wind scaling per direction -------------------------------------------
    dir_ratios: dict[str, list[float]] = defaultdict(list)
    all_ratios: list[float] = []
    for day, f, m in pairs:
        fw, mw = _num(f.get("wind_max")), _num(m.get("wind_max"))
        if fw and fw > 1 and mw is not None:
            ratio = mw / fw
            all_ratios.append(ratio)
            sec = _sector(f.get("wind_dir"))
            if sec:
                dir_ratios[sec].append(ratio)
    wind_scale = {sec: round(_avg(r), 3) for sec, r in dir_ratios.items() if r}
    wind_scale["overall"] = round(_avg(all_ratios), 3) if all_ratios else 1.0

    # Statistics (rain forecast accuracy + temp MAE) --------------------------
    correct = 0
    total = 0
    abs_tmax = []
    for day, f, m in pairs:
        prob = _num(f.get("precip_prob"))
        if prob is not None:
            total += 1
            predicted = prob >= 50
            actual = (_num(m.get("rain")) or 0) >= RAIN_THRESHOLD_MM
            correct += int(predicted == actual)
        if _num(f.get("tmax")) is not None and _num(m.get("tmax")) is not None:
            abs_tmax.append(abs(m["tmax"] - f["tmax"]))

    statistics = {
        "active": True,
        "days_of_data": n,
        "confidence": round(confidence, 2),
        "rain_forecast_accuracy": round(correct / total, 3) if total else None,
        "avg_temp_deviation": round(_avg(abs_tmax), 2) if abs_tmax else None,
        "typical_wind_correction": wind_scale.get("overall", 1.0),
    }

    return {
        "days_of_data": n,
        "active": True,
        "confidence": round(confidence, 2),
        "temp_bias": temp_bias,
        "temp_bias_overall": temp_bias_overall,
        "rain": rain,
        "wind_scale": wind_scale,
        "statistics": statistics,
    }


def _build_cached() -> dict:
    now = time.monotonic()
    hit = _cache.get("model")
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    try:
        model = _build()
    except Exception as exc:  # noqa: BLE001
        log.warning("model build failed: %s", exc)
        model = {"days_of_data": 0, "active": False, "confidence": 0.0}
    _cache["model"] = (now, model)
    return model


# ── Public API used by api.py ──────────────────────────────────────────────
def get_corrections() -> dict:
    """Return the correction model, or {} if not enough data to activate."""
    model = _build_cached()
    return model if model.get("active") else {}


def apply(day: dict, corrections: dict) -> tuple[dict, list]:
    """
    Apply corrections to one forecast day. Returns (corrected_values, badges).
    `badges` is a list of {type, text} for the UI; empty when no correction is
    meaningful. Safe to call with corrections={} (returns the day unchanged).
    """
    if not corrections:
        return {}, []

    month = str(int(day["date"][5:7]))
    bias = (corrections.get("temp_bias") or {}).get(month) or corrections.get(
        "temp_bias_overall", {}
    )
    corrected: dict = {}
    badges: list = []

    tmax = day.get("temp_max")
    tmin = day.get("temp_min")
    if isinstance(tmax, (int, float)) and bias.get("tmax") is not None:
        corrected["temp_max"] = round(tmax + bias["tmax"], 1)
    if isinstance(tmin, (int, float)) and bias.get("tmin") is not None:
        corrected["temp_min"] = round(tmin + bias["tmin"], 1)
    if bias.get("tmax") is not None and abs(bias["tmax"]) >= 1.0:
        sign = "+" if bias["tmax"] > 0 else ""
        badges.append({"type": "temp", "text": f"lokal {sign}{bias['tmax']:.0f}°C"})

    # Rain: replace regional probability with the learned local hit rate.
    prob = day.get("precip_prob")
    rain = corrections.get("rain") or {}
    if isinstance(prob, (int, float)) and prob >= 50 and rain.get("p_rain_given_forecast") is not None:
        local = round(rain["p_rain_given_forecast"] * 100)
        corrected["precip_prob"] = local
        if abs(local - prob) >= 15:
            badges.append({"type": "rain", "text": f"Regen {local}% statt {int(prob)}%"})

    return corrected, badges


def get_statistics() -> dict:
    """Stats for the microclimate dashboard section."""
    model = _build_cached()
    if not model.get("active"):
        return {
            "active": False,
            "days_of_data": model.get("days_of_data", 0),
            "days_needed": MIN_DAYS,
            "message": "learning",
        }
    return model.get("statistics", {})


# ── small numeric helpers ──────────────────────────────────────────────────
def _num(v):
    return v if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)) else None


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
