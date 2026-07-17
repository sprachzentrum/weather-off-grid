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
import settings_store

log = logging.getLogger("microclimate")

MIN_DAYS = 30            # do not correct anything below this
FULL_CONFIDENCE_DAYS = 90
LOOKBACK_DAYS = 400      # ~13 months so every calendar month can contribute
RAIN_THRESHOLD_MM = 1.0  # measured rain above this counts as "it rained"
MAX_TEMP_DIFF = 20.0     # °C; a larger forecast-vs-measured gap means a
                         # mispaired / unit-corrupt day -> drop it from the bias
FAHRENHEIT_SUSPECT = 45.0  # median daily max above this => data looks like °F
_CACHE_TTL = 3600        # rebuild model at most hourly

_cache: dict[str, tuple[float, dict]] = {}

SECTORS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _sector(deg) -> str | None:
    if not isinstance(deg, (int, float)):
        return None
    return SECTORS[int((deg % 360) / 45.0 + 0.5) % 8]


# ── InfluxDB readers ───────────────────────────────────────────────────────
def _site_line(site_id: str | None) -> str:
    """Site restriction as an `and` clause. The default site also claims
    untagged (historically imported) points so they can be paired."""
    if not site_id:
        return ""
    try:
        is_default = site_id == settings_store.default_site_id()
    except Exception:  # noqa: BLE001
        is_default = False
    if is_default:
        return f' and (r.site_id == "{site_id}" or not exists r.site_id)'
    return f' and r.site_id == "{site_id}"'


def _daily_agg(field: str, fn: str, site_id: str | None) -> dict[str, float]:
    """Per-day aggregate of a measured weather field -> {date: value}.

    Days are the site's *local* calendar days: the Flux `location` option makes
    aggregateWindow(every: 1d) cut at local midnight instead of UTC midnight,
    so measured days line up with the forecast target_date (a UTC window starts
    at 21:00 local in Argentina and would smear evening data into the next day).

    timeSrc:"_start" labels each daily window with its start day; the default
    "_stop" would label it with the next day's midnight and shift every measured
    day +1 relative to the forecast target_date, mispairing the data.
    """
    tz_name, tz = db.site_tz(site_id)
    flux = f'''{db.flux_location(tz_name)}
    from(bucket: "{config.BUCKET_WEATHER}")
      |> range(start: -{LOOKBACK_DAYS}d)
      |> filter(fn: (r) => r._measurement == "station" and r._field == "{field}"{_site_line(site_id)})
      |> aggregateWindow(every: 1d, fn: {fn}, timeSrc: "_start", createEmpty: false)
    '''
    out: dict[str, float] = {}
    try:
        for table in db.query(flux):
            for rec in table.records:
                value = rec.get_value()
                if value is not None:
                    # Window starts are local midnights returned as UTC instants;
                    # convert back to the site tz before taking the date.
                    out[rec.get_time().astimezone(tz).date().isoformat()] = float(value)
    except Exception as exc:  # noqa: BLE001
        log.debug("measured agg %s/%s failed: %s", field, fn, exc)
    return out


def _measured_daily(site_id: str | None) -> dict[str, dict]:
    tmax = _daily_agg("temperature_outdoor", "max", site_id)
    tmin = _daily_agg("temperature_outdoor", "min", site_id)
    rain = _daily_agg("rain_daily", "max", site_id)  # cumulative counter -> day total
    wind = _daily_agg("wind_speed", "max", site_id)
    wdir = _daily_agg("wind_direction", "mean", site_id)
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


def _forecast_daily(site_id: str | None) -> dict[str, dict]:
    """1-day-ahead archived forecasts keyed by their target date."""
    flux = f'''
    from(bucket: "{config.BUCKET_FORECASTS}")
      |> range(start: -{LOOKBACK_DAYS}d)
      |> filter(fn: (r) => r._measurement == "forecast_daily" and r.lead_days == "1"{_site_line(site_id)})
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
def _build(site_id: str | None) -> dict:
    measured = _measured_daily(site_id)
    forecast = _forecast_daily(site_id)
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
    # Use the MEDIAN of (measured - forecast) and drop implausible gaps. A few
    # mispaired or unit-corrupt days would otherwise drag the mean to absurd
    # values like -11°C; the median is robust to those outliers.
    month_tmax: dict[int, list[float]] = defaultdict(list)
    month_tmin: dict[int, list[float]] = defaultdict(list)
    all_tmax_diff: list[float] = []
    all_tmin_diff: list[float] = []
    m_tmax_samples: list[float] = []
    f_tmax_samples: list[float] = []
    sample_pairs: list[dict] = []
    dropped_outliers = 0
    for day, f, m in pairs:
        month = int(day[5:7])
        mtx, ftx = _num(m.get("tmax")), _num(f.get("tmax"))
        mtn, ftn = _num(m.get("tmin")), _num(f.get("tmin"))
        if mtx is not None and ftx is not None:
            m_tmax_samples.append(mtx)
            f_tmax_samples.append(ftx)
            d = mtx - ftx
            if abs(d) <= MAX_TEMP_DIFF:
                month_tmax[month].append(d)
                all_tmax_diff.append(d)
            else:
                dropped_outliers += 1
            if len(sample_pairs) < 8:
                sample_pairs.append({
                    "date": day, "forecast_tmax": round(ftx, 1),
                    "measured_tmax": round(mtx, 1), "diff": round(d, 1),
                })
        if mtn is not None and ftn is not None:
            d = mtn - ftn
            if abs(d) <= MAX_TEMP_DIFF:
                month_tmin[month].append(d)
                all_tmin_diff.append(d)

    temp_bias = {
        str(mo): {
            "tmax": round(_median(month_tmax.get(mo, [])), 2),
            "tmin": round(_median(month_tmin.get(mo, [])), 2),
        }
        for mo in range(1, 13)
        if month_tmax.get(mo) or month_tmin.get(mo)
    }
    temp_bias_overall = {
        "tmax": round(_median(all_tmax_diff), 2),
        "tmin": round(_median(all_tmin_diff), 2),
    }

    # Unit sanity: a median daily max above ~45°C is almost certainly °F that was
    # imported as if it were °C. Surfaced in the debug block so it is visible.
    median_measured_tmax = _median(m_tmax_samples)
    unit_warning = bool(m_tmax_samples) and median_measured_tmax > FAHRENHEIT_SUSPECT

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
            # Key by the FORECAST wind direction: that is what we know at apply
            # time (the measured direction is unknown when correcting a forecast).
            sec = _sector(f.get("wind_dir"))
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
    # Median of plausible ratios only. A sheltered valley station legitimately
    # reads well below an open-terrain forecast, but ratios outside 0.05..5 are
    # almost certainly bad pairs (sensor dropout, calm-day division), so drop them.
    dir_ratios: dict[str, list[float]] = defaultdict(list)
    all_ratios: list[float] = []
    for day, f, m in pairs:
        fw, mw = _num(f.get("wind_max")), _num(m.get("wind_max"))
        if fw and fw > 1 and mw is not None:
            ratio = mw / fw
            if 0.05 <= ratio <= 5.0:
                all_ratios.append(ratio)
                sec = _sector(f.get("wind_dir"))
                if sec:
                    dir_ratios[sec].append(ratio)
    wind_scale = {sec: round(_median(r), 3) for sec, r in dir_ratios.items() if r}
    wind_scale["overall"] = round(_median(all_ratios), 3) if all_ratios else 1.0

    # Statistics (rain forecast accuracy + temp MAE on filtered pairs) ---------
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
    for d in all_tmax_diff:
        abs_tmax.append(abs(d))

    debug = {
        "pairs": n,
        "pairs_used_temp": len(all_tmax_diff),
        "dropped_temp_outliers": dropped_outliers,
        "measured_tmax_median": round(median_measured_tmax, 1) if m_tmax_samples else None,
        "forecast_tmax_median": round(_median(f_tmax_samples), 1) if f_tmax_samples else None,
        "unit_warning": unit_warning,
        "sample_pairs": sample_pairs,
    }

    statistics = {
        "active": True,
        "days_of_data": n,
        "confidence": round(confidence, 2),
        "rain_forecast_accuracy": round(correct / total, 3) if total else None,
        "avg_temp_deviation": round(_median(abs_tmax), 2) if abs_tmax else None,
        "typical_wind_correction": wind_scale.get("overall", 1.0),
        # Exposed for debugging the correction values directly from /api/microclimate.
        "temp_bias": temp_bias,
        "temp_bias_overall": temp_bias_overall,
        "wind_scale": wind_scale,
        "rain": rain,
        "debug": debug,
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


def _build_cached(site_id: str | None) -> dict:
    key = site_id or "default"
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    try:
        model = _build(site_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("model build failed: %s", exc)
        model = {"days_of_data": 0, "active": False, "confidence": 0.0}
    _cache[key] = (now, model)
    return model


# ── Public API used by api.py ──────────────────────────────────────────────
def get_corrections(site_id: str | None = None) -> dict:
    """Return the correction model for a site, or {} if not enough data."""
    model = _build_cached(site_id)
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

    # Temperature: additive monthly bias (e.g. 18°C - 2°C = 16°C).
    tmax = day.get("temp_max")
    tmin = day.get("temp_min")
    if isinstance(tmax, (int, float)) and bias.get("tmax") is not None:
        corrected["temp_max"] = round(tmax + bias["tmax"], 1)
    if isinstance(tmin, (int, float)) and bias.get("tmin") is not None:
        corrected["temp_min"] = round(tmin + bias["tmin"], 1)
    if bias.get("tmax") is not None and abs(bias["tmax"]) >= 1.0:
        sign = "+" if bias["tmax"] > 0 else ""
        badges.append({"type": "temp", "text": f"lokal {sign}{bias['tmax']:.0f}°C"})

    # Dominant wind direction of the forecast day - drives both the rain factor
    # and the wind scaling (topography channels both differently per direction).
    sector = _sector(day.get("wind_dir"))

    # Rain: multiply the regional probability by the learned local factor
    # (direction-specific when available), e.g. 70% x 0.3 = 21%.
    prob = day.get("precip_prob")
    rain = corrections.get("rain") or {}
    factor = (rain.get("by_dir") or {}).get(sector) if sector else None
    if factor is None:
        factor = rain.get("p_rain_given_forecast")
    if isinstance(prob, (int, float)) and prob >= 50 and factor is not None:
        local = max(0, min(100, round(prob * factor)))
        corrected["precip_prob"] = local
        if abs(local - prob) >= 15:
            badges.append({"type": "rain", "text": f"Regen {local}% statt {int(prob)}%"})

    # Wind: multiplicative scaling by dominant direction (e.g. 15 x 1.4 = 21 km/h).
    wind_scale = corrections.get("wind_scale") or {}
    scale = wind_scale.get(sector) if sector else None
    if scale is None:
        scale = wind_scale.get("overall")
    if scale is not None:
        wmax = day.get("wind_max")
        gmax = day.get("gust_max")
        if isinstance(wmax, (int, float)):
            corrected["wind_max"] = round(wmax * scale, 1)
        if isinstance(gmax, (int, float)):
            corrected["gust_max"] = round(gmax * scale, 1)
        if abs(scale - 1.0) >= 0.15:
            label = f"{round(scale, 2):g}".replace(".", ",")
            badges.append({"type": "wind", "text": f"Wind ×{label}"})

    return corrected, badges


def invalidate(site_id: str | None = None) -> None:
    """Drop cached model(s) so the next request rebuilds from fresh data."""
    if site_id:
        _cache.pop(site_id, None)
    else:
        _cache.clear()


def get_statistics(site_id: str | None = None) -> dict:
    """Stats for the microclimate dashboard section."""
    model = _build_cached(site_id)
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


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0
