"""
REST API endpoints consumed by the PWA frontend and the iOS widget.

Every data endpoint takes an optional ?site=<site_id> query parameter. When
omitted, the default site (settings_store.default_site_id) is used. Weather and
energy "current"/"history" data come from InfluxDB filtered by the site's
site_id tag; forecast data comes live from Open-Meteo using the site's own
coordinates (cached per site). The microclimate model is computed per site.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

import httpx
from fastapi import APIRouter, HTTPException, Query

import config
import db
import settings_store
from collectors import openmeteo_collector as om
from forecast import microclimate
from forecast import barometric

log = logging.getLogger("api")
router = APIRouter(prefix="/api")

# ── Shared Open-Meteo client + per-site TTL cache ──────────────────────────
_client: httpx.AsyncClient | None = None
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 300  # seconds


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient()
    return _client


def invalidate_cache() -> None:
    """Drop the per-site forecast cache (called after settings change)."""
    _cache.clear()


def _resolve_site(site_id: str | None) -> dict:
    site = settings_store.get_site(site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="no sites configured")
    return site


async def _forecast_raw(site: dict) -> dict:
    """Cached raw Open-Meteo forecast for a site (current + hourly + daily)."""
    sid = site["site_id"]
    now = time.monotonic()
    hit = _cache.get(sid)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    raw = await om.fetch_forecast(
        _get_client(), site.get("latitude"), site.get("longitude"), site.get("timezone")
    )
    _cache[sid] = (now, raw)
    return raw


def _today_iso(raw: dict) -> str:
    cur = (raw.get("current") or {}).get("time")
    return cur[:10] if cur else date.today().isoformat()


def _round(value, ndigits: int = 1):
    return round(value, ndigits) if isinstance(value, (int, float)) else value


def _parse_iso(t: str):
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _integrate_kwh(points: list[tuple]) -> float | None:
    """Trapezoidal integral of power (W) over time -> energy (kWh).

    points: [(datetime, watts), ...]. Gaps longer than 2 h are skipped so a
    sensor outage doesn't invent energy.
    """
    pts = sorted((t, v) for t, v in points if t is not None and isinstance(v, (int, float)))
    if len(pts) < 2:
        return None
    wh = 0.0
    for (t0, p0), (t1, p1) in zip(pts, pts[1:]):
        dt_h = (t1 - t0).total_seconds() / 3600.0
        if 0 < dt_h <= 2:
            wh += (p0 + p1) / 2.0 * dt_h
    return round(wh / 1000.0, 2)


def _site_midnight_utc(site: dict) -> datetime:
    """Start of the current local day for a site, as a UTC datetime."""
    tz = timezone.utc
    if ZoneInfo is not None:
        try:
            tz = ZoneInfo(site.get("timezone") or "UTC")
        except Exception:  # noqa: BLE001
            tz = timezone.utc
    now_local = datetime.now(tz)
    midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(timezone.utc)


def _energy_since_midnight(site: dict, field: str) -> float | None:
    """Integrate a power field (W) since local midnight into kWh from InfluxDB."""
    series = db.series(
        config.BUCKET_ENERGY, "energy", [field],
        site_id=site["site_id"], days=2, every="15m",
    )
    midnight = _site_midnight_utc(site)
    points = []
    for t, v in zip(series.get("time", []), series.get(field, [])):
        dt = _parse_iso(t)
        if dt is not None and v is not None and dt >= midnight:
            points.append((dt, v))
    return _integrate_kwh(points)


# ── SOC-based autonomy helpers ─────────────────────────────────────────────
def _site_tz(site: dict):
    """The site's tzinfo (falls back to UTC)."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo(site.get("timezone") or "UTC")
        except Exception:  # noqa: BLE001
            pass
    return timezone.utc


def _parse_local(t: str, tz) -> datetime | None:
    """Parse an Open-Meteo local timestamp (e.g. '2026-06-18T07:45') as UTC.

    Open-Meteo returns sunrise/sunset without an offset when queried with a
    timezone, so we attach the site tz and convert to UTC for comparison with
    the UTC-stamped InfluxDB readings.
    """
    dt = _parse_iso(t)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def _soc_series(site: dict, days: int = 9) -> list[tuple[datetime, float]]:
    """SOC readings over the last `days`, as a time-sorted [(utc_dt, soc)]."""
    s = db.series(
        config.BUCKET_ENERGY, "energy", ["battery_soc"],
        site_id=site["site_id"], days=days, every="15m",
    )
    out: list[tuple[datetime, float]] = []
    for t, v in zip(s.get("time", []), s.get("battery_soc", [])):
        dt = _parse_iso(t)
        if dt is not None and isinstance(v, (int, float)):
            out.append((dt, float(v)))
    out.sort(key=lambda p: p[0])
    return out


def _soc_at(series: list[tuple[datetime, float]], target: datetime,
            tol_minutes: float = 90) -> float | None:
    """Nearest SOC reading to `target`, or None if none within `tol_minutes`."""
    best = None
    best_gap = None
    for dt, v in series:
        gap = abs((dt - target).total_seconds())
        if best_gap is None or gap < best_gap:
            best_gap, best = gap, v
    if best_gap is not None and best_gap <= tol_minutes * 60:
        return best
    return None


def _solar_kwh_by_day(raw: dict, pv_kwp: float, pv_eff: float) -> dict[str, float]:
    """Estimated PV yield (kWh) per local day from forecast shortwave radiation.

    Same PSH method as /forecast/solar, but returns a {date: kWh} map covering
    past_days + forecast so the autonomy simulation can look ahead.
    """
    h = raw.get("hourly") or {}
    times = h.get("time") or []
    radiation = h.get("shortwave_radiation") or []
    per_day: dict[str, list[float]] = {}
    for i, t in enumerate(times):
        val = radiation[i] if i < len(radiation) else None
        if val is None:
            continue
        per_day.setdefault(t[:10], []).append(float(val))
    return {day: (sum(ghi) / 1000.0) * pv_kwp * pv_eff for day, ghi in per_day.items()}


# ── /api/sites ─────────────────────────────────────────────────────────────
@router.get("/sites")
async def list_sites():
    """All configured sites (public fields only) + the default site id."""
    fc = settings_store.frontend_config()
    return {"sites": fc["sites"], "default_site": fc["default_site"]}


# ── /api/current ───────────────────────────────────────────────────────────
@router.get("/current")
async def current(site: str | None = Query(None)):
    s = _resolve_site(site)
    sid = s["site_id"]
    weather = db.latest_fields(config.BUCKET_WEATHER, "station", site_id=sid)
    energy = db.latest_fields(config.BUCKET_ENERGY, "energy", site_id=sid)

    raw = {}
    try:
        raw = await _forecast_raw(s)
    except Exception as exc:  # noqa: BLE001
        log.warning("[%s] forecast fetch for /current failed: %s", sid, exc)

    cur = raw.get("current") or {}
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
        "site_id": sid,
        "station_name": s.get("name"),
        "updated": updated,
        "weather": weather,
        "battery": battery,
        "sun": {"sunrise": sunrise, "sunset": sunset},
        "weather_code": cur.get("weather_code"),
        "is_day": cur.get("is_day"),
    }


# ── /api/forecast ──────────────────────────────────────────────────────────
@router.get("/forecast")
async def forecast(site: str | None = Query(None)):
    s = _resolve_site(site)
    raw = await _forecast_raw(s)
    daily = raw.get("daily") or {}
    times = daily.get("time") or []
    today = _today_iso(raw)

    corrections = microclimate.get_corrections(s["site_id"])
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
        "site_id": s["site_id"],
        "days": days,
        "microclimate": {
            "active": bool(corrections),
            "confidence": corrections.get("confidence", 0.0) if corrections else 0.0,
        },
    }


# ── /api/forecast/hourly ───────────────────────────────────────────────────
@router.get("/forecast/hourly")
async def forecast_hourly(site: str | None = Query(None), hours: int = Query(24, ge=1, le=72)):
    s = _resolve_site(site)
    raw = await _forecast_raw(s)
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
    return {"site_id": s["site_id"], "hours": out}


# ── /api/forecast/solar (PSH, not sunshine_duration!) ──────────────────────
@router.get("/forecast/solar")
async def forecast_solar(
    site: str | None = Query(None),
    pv_kwp: float = Query(None, ge=0),
    pv_eff: float = Query(None, ge=0, le=1),
):
    """
    Per-day solar yield estimate from Peak Sun Hours.

    PSH = sum(shortwave_radiation[W/m²] over the day) / 1000. We deliberately do
    NOT use Open-Meteo's `sunshine_duration`, which badly overestimates usable
    yield. Defaults for pv_kwp/pv_eff come from the site, overridable per request.
    """
    s = _resolve_site(site)
    pv_kwp = s.get("pv_kwp", config.PV_KWP) if pv_kwp is None else pv_kwp
    pv_eff = s.get("pv_efficiency", config.PV_EFFICIENCY) if pv_eff is None else pv_eff

    raw = await _forecast_raw(s)
    h = raw.get("hourly") or {}
    times = h.get("time") or []
    radiation = h.get("shortwave_radiation") or []
    today = _today_iso(raw)

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
        production_window = sum(1 for v in ghi if v > 100)
        estimated_kwh = psh * pv_kwp * pv_eff
        days.append({
            "date": day,
            "psh": round(psh, 2),
            "estimated_kwh": round(estimated_kwh, 1),
            "production_window_hours": production_window,
            "rating": "good" if psh > 4 else "fair" if psh >= 2 else "poor",
        })

    return {"site_id": s["site_id"], "pv_kwp": pv_kwp, "pv_efficiency": pv_eff, "days": days[:7]}


# ── /api/history ───────────────────────────────────────────────────────────
@router.get("/history")
async def history(site: str | None = Query(None), days: int = Query(7, ge=1, le=90)):
    s = _resolve_site(site)
    every = "1h" if days <= 14 else "6h"
    return db.series(
        config.BUCKET_WEATHER,
        "station",
        [
            "temperature_outdoor", "wind_speed", "wind_gust", "rain_rate",
            "rain_daily", "pressure_relative", "solar_radiation",
        ],
        site_id=s["site_id"],
        days=days,
        every=every,
    )


# ── /api/battery ───────────────────────────────────────────────────────────
@router.get("/battery")
async def battery(site: str | None = Query(None), days: int = Query(7, ge=1, le=90)):
    s = _resolve_site(site)
    sid = s["site_id"]
    latest = db.latest_fields(config.BUCKET_ENERGY, "energy", site_id=sid)
    latest.pop("_time", None)
    series = db.series(
        config.BUCKET_ENERGY,
        "energy",
        ["battery_soc", "pv_power", "load_power", "battery_power"],
        site_id=sid,
        days=days,
        every="1h" if days <= 14 else "6h",
    )
    return {
        "site_id": sid,
        "soc": _round(latest.get("battery_soc")),
        "pv_power": _round(latest.get("pv_power")),
        "load_power": _round(latest.get("load_power")),
        "battery_power": _round(latest.get("battery_power")),
        "latest": latest,
        "series": series,
    }


# ── /api/energy/today ──────────────────────────────────────────────────────
@router.get("/energy/today")
async def energy_today(site: str | None = Query(None)):
    s = _resolve_site(site)
    latest = db.latest_fields(config.BUCKET_ENERGY, "energy", site_id=s["site_id"])

    # The inverter's *_Today counters are unreliable on SPF (often stuck at 0),
    # so fall back to integrating the stored power readings since local midnight.
    load_e = latest.get("load_energy_today")
    load_src = "inverter"
    if not load_e:
        load_e = _energy_since_midnight(s, "load_power")
        load_src = "integrated"

    pv_e = latest.get("pv_energy_today")
    pv_src = "inverter"
    if not pv_e:
        pv_e = _energy_since_midnight(s, "pv_power")
        pv_src = "integrated"

    return {
        "site_id": s["site_id"],
        "pv_energy_today": _round(pv_e, 2),
        "load_energy_today": _round(load_e, 2),
        "battery_soc": _round(latest.get("battery_soc")),
        "source": {"pv": pv_src, "load": load_src},
    }


# ── /api/energy/autonomy ───────────────────────────────────────────────────
RESERVE_PCT = 10.0  # battery floor we never want to fall below


@router.get("/energy/autonomy")
async def energy_autonomy(
    site: str | None = Query(None),
    capacity_kwh: float = Query(None, ge=0),
    pv_kwp: float = Query(None, ge=0),
    pv_eff: float = Query(None, ge=0, le=1),
):
    """
    Autonomy derived from the SOC history, not from momentary load.

    The real rhythm of an off-grid household is captured by how the battery
    actually moves: it drains overnight (battery-only, no PV) and recovers by
    day. We measure that directly from the SOC curve in InfluxDB:

      1. Night consumption  = SOC(sunset) - SOC(sunrise), averaged over the last
         7 nights → the true draw with no PV contamination.
      2. Daily balance      = SOC delta over 24 h (18:00→18:00), averaged → tells
         whether the system is net gaining or losing.
      3. Autonomy (with PV) = forward-simulate SOC using the solar forecast for
         the coming days; report the days until SOC hits the 10 % reserve.
         Autonomy (no PV)   = SOC × capacity / night-consumption rate (worst case).
    """
    s = _resolve_site(site)
    sid = s["site_id"]
    capacity = s.get("battery_capacity_kwh", config.BATTERY_CAPACITY_KWH) if capacity_kwh is None else capacity_kwh
    pv_kwp = s.get("pv_kwp", config.PV_KWP) if pv_kwp is None else pv_kwp
    pv_eff = s.get("pv_efficiency", config.PV_EFFICIENCY) if pv_eff is None else pv_eff

    latest = db.latest_fields(config.BUCKET_ENERGY, "energy", site_id=sid)
    soc = latest.get("battery_soc")
    reserve_kwh = (RESERVE_PCT / 100.0) * capacity

    socs = _soc_series(s, days=9)
    tz = _site_tz(s)
    now_utc = datetime.now(timezone.utc)

    raw = {}
    try:
        raw = await _forecast_raw(s)
    except Exception as exc:  # noqa: BLE001
        log.warning("[%s] forecast fetch for /autonomy failed: %s", sid, exc)
    daily = raw.get("daily") or {}
    days_iso = daily.get("time") or []
    sunrises = daily.get("sunrise") or []
    sunsets = daily.get("sunset") or []

    # 1. Night consumption: SOC drop from each sunset to the next sunrise.
    nights: list[dict] = []
    for i in range(len(days_iso) - 1):
        sunset = _parse_local(sunsets[i], tz) if i < len(sunsets) else None
        sunrise = _parse_local(sunrises[i + 1], tz) if i + 1 < len(sunrises) else None
        if sunset is None or sunrise is None or sunrise >= now_utc:
            continue
        soc_set = _soc_at(socs, sunset)
        soc_rise = _soc_at(socs, sunrise)
        if soc_set is None or soc_rise is None:
            continue
        drop_pct = soc_set - soc_rise
        hours = (sunrise - sunset).total_seconds() / 3600.0
        if drop_pct <= 0 or hours <= 0:
            continue  # charged overnight (generator/grid) → not a clean sample
        kwh = drop_pct / 100.0 * capacity
        nights.append({"kwh": kwh, "hours": hours, "power_w": kwh * 1000.0 / hours})
    nights = nights[-7:]

    night_kwh = night_power_w = night_hours = None
    if nights:
        n = len(nights)
        night_kwh = sum(x["kwh"] for x in nights) / n
        night_hours = sum(x["hours"] for x in nights) / n
        night_power_w = sum(x["power_w"] for x in nights) / n

    # 2. Daily balance: SOC delta over rolling 18:00→18:00 windows.
    balances: list[float] = []
    today_local = datetime.now(tz).date()
    for back in range(1, 8):
        end_local = datetime.combine(today_local, datetime.min.time(), tz).replace(hour=18) \
            - timedelta(days=back - 1)
        start_local = end_local - timedelta(days=1)
        if end_local.astimezone(timezone.utc) > now_utc:
            continue
        soc_end = _soc_at(socs, end_local.astimezone(timezone.utc))
        soc_start = _soc_at(socs, start_local.astimezone(timezone.utc))
        if soc_end is None or soc_start is None:
            continue
        balances.append((soc_end - soc_start) / 100.0 * capacity)
    daily_balance_kwh = (sum(balances) / len(balances)) if balances else None

    # Average daily consumption from the energy balance: consumption = PV - ΔSOC.
    # PV is integrated over whole local days so the windows line up.
    daily_consumption_kwh = None
    consumption_source = None
    pv_series = db.series(config.BUCKET_ENERGY, "energy", ["pv_power"], site_id=sid, days=9, every="15m")
    last_midnight = _site_midnight_utc(s)
    win_days = 7
    win_start = last_midnight - timedelta(days=win_days)
    pv_pts = [
        (_parse_iso(t), v) for t, v in zip(pv_series.get("time", []), pv_series.get("pv_power", []))
        if _parse_iso(t) is not None and isinstance(v, (int, float))
        and win_start <= _parse_iso(t) <= last_midnight
    ]
    pv_kwh_win = _integrate_kwh(pv_pts)
    soc_start_win = _soc_at(socs, win_start)
    soc_end_win = _soc_at(socs, last_midnight)
    if pv_kwh_win is not None and soc_start_win is not None and soc_end_win is not None:
        soc_change_kwh = (soc_end_win - soc_start_win) / 100.0 * capacity
        cons = pv_kwh_win - soc_change_kwh
        if cons > 0:
            daily_consumption_kwh = cons / win_days
            consumption_source = "energy_balance"
    if daily_consumption_kwh is None and night_power_w:
        # Fallback: extrapolate the night baseline across a full 24 h.
        daily_consumption_kwh = night_power_w * 24.0 / 1000.0
        consumption_source = "night_extrapolated"

    # 3a. Autonomy with PV: forward-simulate from now using the solar forecast.
    autonomy_days = None
    autonomy_capped = False
    if soc is not None and daily_consumption_kwh:
        solar = _solar_kwh_by_day(raw, pv_kwp, pv_eff)
        forecast_days = sorted(d for d in solar if d >= today_local.isoformat())
        soc_kwh = soc / 100.0 * capacity
        days = 0.0
        if soc_kwh <= reserve_kwh:
            autonomy_days = 0.0
        else:
            for day in forecast_days:
                drain = daily_consumption_kwh - solar.get(day, 0.0)  # +ve = battery falls
                if drain <= 0:
                    soc_kwh = min(capacity, soc_kwh - drain)
                    days += 1
                    continue
                if soc_kwh - drain >= reserve_kwh:
                    soc_kwh -= drain
                    days += 1
                else:
                    days += max(0.0, (soc_kwh - reserve_kwh) / drain)
                    autonomy_days = round(days, 1)
                    break
            if autonomy_days is None:  # survived the whole forecast horizon
                autonomy_days = round(days, 1)
                autonomy_capped = True

    # 3b. Autonomy without PV: usable energy / night-consumption rate.
    autonomy_no_pv_days = None
    if soc is not None and night_power_w:
        usable_kwh = max(0.0, (soc - RESERVE_PCT) / 100.0 * capacity)
        autonomy_no_pv_days = usable_kwh / (night_power_w / 1000.0) / 24.0

    status = None
    if autonomy_days is not None:
        status = "green" if autonomy_days > 2 else "yellow" if autonomy_days >= 1 else "red"

    return {
        "site_id": sid,
        "soc": _round(soc),
        "capacity_kwh": capacity,
        "reserve_pct": RESERVE_PCT,
        "autonomy_days": _round(autonomy_days, 1),
        "autonomy_capped": autonomy_capped,
        "autonomy_no_pv_days": _round(autonomy_no_pv_days, 1),
        "status": status,
        "night_consumption_kwh": _round(night_kwh, 2),
        "night_power_w": _round(night_power_w, 0),
        "night_hours": _round(night_hours, 1),
        "nights_used": len(nights),
        "daily_balance_kwh": _round(daily_balance_kwh, 2),
        "daily_consumption_kwh": _round(daily_consumption_kwh, 2),
        "consumption_source": consumption_source,
        # Backwards-compatible field (PV-aware days expressed in hours).
        "hours_remaining": _round(autonomy_days * 24, 1) if autonomy_days is not None else None,
    }


# ── /api/microclimate ──────────────────────────────────────────────────────
@router.get("/microclimate")
async def microclimate_stats(site: str | None = Query(None)):
    s = _resolve_site(site)
    return microclimate.get_statistics(s["site_id"])


# ── /api/local-forecast (Zambretti, from local sensors) ────────────────────
@router.get("/local-forecast")
async def local_forecast(site: str | None = Query(None)):
    """
    Short-term (6-12 h) barometric forecast from the local station only:
    pressure trend + Zambretti. Works offline; the Open-Meteo comparison is
    best-effort and omitted when offline.
    """
    s = _resolve_site(site)
    sid = s["site_id"]
    latest = db.latest_fields(config.BUCKET_WEATHER, "station", site_id=sid)
    pressure = latest.get("pressure_relative")
    wind_dir = latest.get("wind_direction")

    lt = latest.get("_time")
    month = lt.month if hasattr(lt, "month") else datetime.utcnow().month

    # 30-min pressure series over the last 24 h: drives both the trend and the graph.
    series = db.series(
        config.BUCKET_WEATHER, "station", ["pressure_relative"],
        site_id=sid, days=1, every="30m",
    )
    delta = barometric.trend_3h(series.get("time"), series.get("pressure_relative"))
    trend, arrow = barometric.classify_trend(delta)
    southern = (s.get("latitude") or 0) < 0
    zam = barometric.zambretti(pressure, trend, wind_dir, month, southern=southern)

    # Best-effort comparison with the regional model (rain yes/no today).
    comparison = None
    if zam is not None:
        try:
            raw = await _forecast_raw(s)
            daily = raw.get("daily") or {}
            times = daily.get("time") or []
            if times:
                idx = times.index(_today_iso(raw)) if _today_iso(raw) in times else 0
                probs = daily.get("precipitation_probability_max") or []
                om_prob = probs[idx] if idx < len(probs) else None
                if om_prob is not None:
                    comparison = {
                        "openmeteo_rain_prob": om_prob,
                        "openmeteo_rain": om_prob >= 50,
                        "zambretti_rain": zam["rain_likely"],
                        "agree": zam["rain_likely"] == (om_prob >= 50),
                    }
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s] local-forecast OM compare failed: %s", sid, exc)

    return {
        "site_id": sid,
        "pressure": _round(pressure),
        "wind_dir": wind_dir,
        "trend_3h": delta,
        "trend": trend,
        "arrow": arrow,
        "southern_hemisphere": southern,
        "zambretti": zam,
        "pressure_series": series,
        "comparison": comparison,
    }
