"""
Open-Meteo collector + shared forecast client.

Two responsibilities:

1. `fetch_forecast()` is the single place that talks to the Open-Meteo forecast
   API. Both the API endpoints (live forecast) and the archiver reuse it, so the
   parameter set - crucially the hourly `shortwave_radiation` used for the PSH
   solar-yield calculation - is defined exactly once.

2. `run_poller()` periodically archives the current 7-day daily forecast into the
   `forecasts` bucket, tagged by target date and lead time. These snapshots are
   later compared against the locally measured weather to learn microclimate
   correction factors.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone

import httpx

import collector_status
import config
import db

log = logging.getLogger("openmeteo")

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Hourly variables. `shortwave_radiation` (GHI, W/m²) is the key field for PSH.
HOURLY_VARS = [
    "temperature_2m",
    "relativehumidity_2m",
    "apparent_temperature",
    "precipitation",
    "precipitation_probability",
    "weathercode",
    "pressure_msl",
    "windspeed_10m",
    "windgusts_10m",
    "winddirection_10m",
    "cloudcover",
    "shortwave_radiation",
    "is_day",
]
DAILY_VARS = [
    "weathercode",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "precipitation_probability_max",
    "windspeed_10m_max",
    "windgusts_10m_max",
    "winddirection_10m_dominant",
    "sunrise",
    "sunset",
    "sunshine_duration",
    "uv_index_max",
]
CURRENT_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "is_day",
    "precipitation",
    "weather_code",
    "cloud_cover",
    "pressure_msl",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
]


async def fetch_forecast(
    client: httpx.AsyncClient,
    latitude: float | None = None,
    longitude: float | None = None,
    timezone_name: str | None = None,
    forecast_days: int = 7,
    past_days: int = 7,
) -> dict:
    """Fetch the raw Open-Meteo forecast JSON (metric units, local timezone).

    Coordinates default to the .env location for backward compatibility, but the
    site-aware callers pass each site's own latitude/longitude/timezone.
    """
    params = {
        "latitude": config.LATITUDE if latitude is None else latitude,
        "longitude": config.LONGITUDE if longitude is None else longitude,
        "timezone": timezone_name or config.TIMEZONE,
        "forecast_days": forecast_days,
        "past_days": past_days,
        "wind_speed_unit": "kmh",
        "timeformat": "iso8601",
        "current": ",".join(CURRENT_VARS),
        "hourly": ",".join(HOURLY_VARS),
        "daily": ",".join(DAILY_VARS),
    }
    resp = await client.get(FORECAST_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def archive_daily(raw: dict, site_id: str = "default") -> int:
    """
    Store each day of the current daily forecast as a snapshot in `forecasts`.
    Tagged by target_date + lead_days so we can later pair forecast with reality.
    Returns the number of points written.
    """
    daily = raw.get("daily") or {}
    times = daily.get("time") or []
    today = date.today()
    written = 0
    for i, day_iso in enumerate(times):
        target = date.fromisoformat(day_iso)
        if target < today:
            continue  # only archive today + future predictions
        lead = (target - today).days
        sunshine = (daily.get("sunshine_duration") or [None] * len(times))[i]
        fields = {
            "temp_max": _at(daily, "temperature_2m_max", i),
            "temp_min": _at(daily, "temperature_2m_min", i),
            "precip_sum": _at(daily, "precipitation_sum", i),
            "precip_prob": _at(daily, "precipitation_probability_max", i),
            "wind_max": _at(daily, "windspeed_10m_max", i),
            "gust_max": _at(daily, "windgusts_10m_max", i),
            "wind_dir": _at(daily, "winddirection_10m_dominant", i),
            "weathercode": _at(daily, "weathercode", i),
            "sunshine_hours": round(sunshine / 3600.0, 2) if sunshine else None,
        }
        fields = {k: v for k, v in fields.items() if v is not None}
        if not fields:
            continue
        db.write_point(
            config.BUCKET_FORECASTS,
            "forecast_daily",
            fields,
            tags={"target_date": day_iso, "lead_days": str(lead), "site_id": site_id},
            ts=datetime(target.year, target.month, target.day, tzinfo=timezone.utc),
        )
        written += 1
    return written


def _at(group: dict, key: str, idx: int):
    seq = group.get(key)
    if isinstance(seq, list) and idx < len(seq):
        return seq[idx]
    return None


async def run_poller(site: dict) -> None:
    """Per-site archiver: snapshots that site's daily forecast into `forecasts`."""
    sid = site["site_id"]
    log.info("[%s] openmeteo archiver started (every %ds)", sid, config.OPENMETEO_POLL_INTERVAL)
    async with httpx.AsyncClient() as client:
        while True:
            try:
                raw = await fetch_forecast(
                    client, site.get("latitude"), site.get("longitude"), site.get("timezone")
                )
                n = archive_daily(raw, sid)
                log.info("[%s] archived %d daily forecast snapshots", sid, n)
                collector_status.record_success(sid, "openmeteo")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("[%s] openmeteo archive failed: %s", sid, exc)
                collector_status.record_error(sid, "openmeteo", exc)
            await asyncio.sleep(config.OPENMETEO_POLL_INTERVAL)
