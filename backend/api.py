"""
REST API endpoints consumed by the PWA frontend and the iOS widget.

Weather/energy "current" and "history" data come from InfluxDB (filled by the
collectors). Forecast data comes live from Open-Meteo via the shared client in
openmeteo_collector, with a short in-process cache so repeated dashboard loads
don't hammer the upstream API. The microclimate model (forecast.microclimate)
adjusts the raw forecast once enough local history exists.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime

import httpx
from fastapi import APIRouter, Query

import config
import db
from collectors import openmeteo_collector as om
from forecast import microclimate

log = logging.getLogger("api")
router = APIRouter(prefix="/api")

# ── Shared Open-Meteo client + tiny TTL cache ──────────────────────────────
_client: httpx.AsyncClient | None = None
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 300  # seconds


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient()
    return _client


async def _forecast_raw() -> dict:
    """Cached raw Open-Meteo forecast (current + hourly + daily, ±7 days)."""
    now = time.monotonic()
    hit = _cache.get("forecast")
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    raw = await om.fetch_forecast(_get_client())
    _cache["forecast"] = (now, raw)
    return raw


def _today_iso(raw: dict) -> str:
    """Station-local 'today' derived from the Open-Meteo current timestamp."""
    cur = (raw.get("current") or {}).get("time")
    if cur:
        return cur[:10]
    return date.today().isoformat()


def _round(value, ndigits: int = 1):
    return round(value, ndigits) if isinstance(value, (int, float)) else value


# ── /api/current ───────────────────────────────────────────────────────────
@router.get("/current")
async def current():
    """Latest measured weather (Ecowitt) + battery (Growatt), Open-Meteo fallback."""
    weather = db.latest_fields(config.BUCKET_WEATHER, "station")
    energy = db.latest_fields(config.BUCKET_ENERGY, "energy")

    raw = {}
    try:
        raw = await _forecast_raw()
    except Exception as exc:  # noqa: BLE001
        log.warning("forecast fetch for /current failed: %s", exc)

    cur = raw.get("current") or {}
    # Fall back to Open-Meteo current values where the station has no data.
    if not weather:
        weather = {
            "temperature_outdoor": cur.get("temperature_2m"),
            "humidity_outdoor": cur.get("relative_humidity_2m"),
            "temperature_feels_like": cur.get("apparent_temperature"),
            "wind_speed": cur.get("wind_speed_10m"),
            "wind_gust": cur.get("wind_gusts_10m"),
            "wind_direction": cur.get("wind_direction_10m"),
            "pressure_relative": cur.get("pressure_msl"),
            "rain_rate": cur.get("precipitation"),
            "source": "openmeteo",
        }
    else:
        weather = dict(weather)
        weather["source"] = "station"

    updated = weather.pop("_time", None)
    if isinstance(updated, datetime):
        updated = updated.isoformat()

    # Sunrise/sunset for today from the daily block.
    daily = raw.get("daily") or {}
    sunrise = sunset = None
    if daily.get("time"):
        try:
            idx = daily["time"].index(_today_iso(raw))
            sunrise = daily.get("sunrise", [None])[idx]
            sunset = daily.get("sunset", [None])[idx]
        except (ValueError, IndexError):
            pass

    battery = None
    if energy:
        et = energy.pop("_time", None)
        battery = dict(energy)
        if isinstance(et, datetime):
            battery["updated"] = et.isoformat()

    return {
        "station_name": None,  # frontend uses CONFIG.STATION_NAME
        "updated": updated,
        "weather": weather,
        "battery": battery,
        "sun": {"sunrise": sunrise, "sunset": sunset},
        "weather_code": cur.get("weather_code"),
        "is_day": cur.get("is_day"),
    }


# ── /api/forecast ──────────────────────────────────────────────────────────
@router.get("/forecast")
async def forecast():
    """7-day daily forecast with microclimate correction badges where available."""
    raw = await _forecast_raw()
    daily = raw.get("daily") or {}
    times = daily.get("time") or []
    today = _today_iso(raw)

    corrections = microclimate.get_corrections()  # {} until enough data
    days = []
    for i, day_iso in enumerate(times):
        if day_iso < today:
            continue
        base = {
            "date": day_iso,
            "weathercode": om._at(daily, "weathercode", i),
            "temp_min": _round(om._at(daily, "temperature_2m_min", i)),
            "temp_max": _round(om._at(daily, "temperature_2m_max", i)),
            "precip_sum": _round(om._at(daily, "precipitation_sum", i)),
            "precip_prob": om._at(daily, "precipitation_probability_max", i),
            "wind_max": _round(om._at(daily, "windspeed_10m_max", i)),
            "gust_max": _round(om._at(daily, "windgusts_10m_max", i)),
            "wind_dir": om._at(daily, "winddirection_10m_dominant", i),
            "sunrise": om._at(daily, "sunrise", i),
            "sunset": om._at(daily, "sunset", i),
        }
        base["corrected"], base["badges"] = microclimate.apply(base, corrections)
        days.append(base)

    return {
        "days": days,
        "microclimate": {
            "active": bool(corrections),
            "confidence": corrections.get("confidence", 0.0) if corrections else 0.0,
        },
    }


# ── /api/forecast/hourly ───────────────────────────────────────────────────
@router.get("/forecast/hourly")
async def forecast_hourly(hours: int = Query(24, ge=1, le=72)):
    """Next N hours (default 24) from the hourly block."""
    raw = await _forecast_raw()
    h = raw.get("hourly") or {}
    times = h.get("time") or []
    now = (raw.get("current") or {}).get("time", "")

    start = next((i for i, t in enumerate(times) if t >= now), 0)
    out = []
    for i in range(start, min(start + hours, len(times))):
        out.append({
            "time": times[i],
            "temp": _round(om._at(h, "temperature_2m", i)),
            "weathercode": om._at(h, "weathercode", i),
            "precip": _round(om._at(h, "precipitation", i)),
            "precip_prob": om._at(h, "precipitation_probability", i),
            "wind": _round(om._at(h, "windspeed_10m", i)),
            "wind_dir": om._at(h, "winddirection_10m", i),
            "is_day": om._at(h, "is_day", i),
        })
    return {"hours": out}


# ── /api/forecast/solar (PSH, not sunshine_duration!) ──────────────────────
@router.get("/forecast/solar")
async def forecast_solar(
    pv_kwp: float = Query(None, ge=0),
    pv_eff: float = Query(None, ge=0, le=1),
):
    """
    Per-day solar yield estimate from Peak Sun Hours.

    PSH = sum(shortwave_radiation[W/m²] over the day) / 1000.
    We deliberately do NOT use Open-Meteo's `sunshine_duration`, which only
    counts hours of direct beam > 120 W/m² and badly overestimates usable yield.
    """
    pv_kwp = config.PV_KWP if pv_kwp is None else pv_kwp
    pv_eff = config.PV_EFFICIENCY if pv_eff is None else pv_eff

    raw = await _forecast_raw()
    h = raw.get("hourly") or {}
    times = h.get("time") or []
    radiation = h.get("shortwave_radiation") or []
    today = _today_iso(raw)

    # Bucket hourly GHI by calendar day.
    per_day: dict[str, list[float]] = {}
    for i, t in enumerate(times):
        day = t[:10]
        if day < today:
            continue
        val = radiation[i] if i < len(radiation) else None
        if val is None:
            continue
        per_day.setdefault(day, []).append(float(val))

    days = []
    for day in sorted(per_day):
        ghi = per_day[day]
        psh = sum(ghi) / 1000.0
        production_window = sum(1 for v in ghi if v > 100)  # hours actually producing
        estimated_kwh = psh * pv_kwp * pv_eff
        days.append({
            "date": day,
            "psh": round(psh, 2),
            "estimated_kwh": round(estimated_kwh, 1),
            "production_window_hours": production_window,
            "rating": "good" if psh > 4 else "fair" if psh >= 2 else "poor",
        })

    return {
        "pv_kwp": pv_kwp,
        "pv_efficiency": pv_eff,
        "days": days[:7],
    }


# ── /api/history ───────────────────────────────────────────────────────────
@router.get("/history")
async def history(days: int = Query(7, ge=1, le=90)):
    """Measured weather time series for the charts."""
    every = "1h" if days <= 14 else "6h"
    return db.series(
        config.BUCKET_WEATHER,
        "station",
        [
            "temperature_outdoor",
            "wind_speed",
            "wind_gust",
            "rain_rate",
            "rain_daily",
            "pressure_relative",
            "solar_radiation",
        ],
        days=days,
        every=every,
    )


# ── /api/battery ───────────────────────────────────────────────────────────
@router.get("/battery")
async def battery(days: int = Query(7, ge=1, le=90)):
    """Battery SOC + PV + load time series, plus the latest snapshot."""
    latest = db.latest_fields(config.BUCKET_ENERGY, "energy")
    latest.pop("_time", None)
    series = db.series(
        config.BUCKET_ENERGY,
        "energy",
        ["battery_soc", "pv_power", "load_power", "battery_power"],
        days=days,
        every="1h" if days <= 14 else "6h",
    )
    return {
        # Flat keys kept for the iOS widget convenience.
        "soc": _round(latest.get("battery_soc")),
        "pv_power": _round(latest.get("pv_power")),
        "load_power": _round(latest.get("load_power")),
        "battery_power": _round(latest.get("battery_power")),
        "latest": latest,
        "series": series,
    }


# ── /api/energy/today ──────────────────────────────────────────────────────
@router.get("/energy/today")
async def energy_today():
    """Daily energy balance from the cumulative Growatt counters."""
    latest = db.latest_fields(config.BUCKET_ENERGY, "energy")
    return {
        "pv_energy_today": _round(latest.get("pv_energy_today"), 2),
        "load_energy_today": _round(latest.get("load_energy_today"), 2),
        "battery_soc": _round(latest.get("battery_soc")),
    }


# ── /api/energy/autonomy ───────────────────────────────────────────────────
@router.get("/energy/autonomy")
async def energy_autonomy(capacity_kwh: float = Query(None, ge=0)):
    """Estimated remaining runtime: SOC * capacity / avg load (last 24h)."""
    capacity = config.BATTERY_CAPACITY_KWH if capacity_kwh is None else capacity_kwh
    latest = db.latest_fields(config.BUCKET_ENERGY, "energy")
    soc = latest.get("battery_soc")

    # Average load over the last 24h (W -> kW).
    avg_load_kw = None
    series = db.series(config.BUCKET_ENERGY, "energy", ["load_power"], days=1, every="1h")
    loads = [v for v in series.get("load_power", []) if isinstance(v, (int, float))]
    if loads:
        avg_load_kw = (sum(loads) / len(loads)) / 1000.0

    hours_remaining = None
    if soc is not None and avg_load_kw and avg_load_kw > 0:
        hours_remaining = (soc / 100.0) * capacity / avg_load_kw

    return {
        "soc": _round(soc),
        "capacity_kwh": capacity,
        "avg_load_kw": _round(avg_load_kw, 3),
        "hours_remaining": _round(hours_remaining, 1),
    }


# ── /api/microclimate ──────────────────────────────────────────────────────
@router.get("/microclimate")
async def microclimate_stats():
    """Correction statistics; empty/learning until >= 30 days of paired data."""
    return microclimate.get_statistics()
