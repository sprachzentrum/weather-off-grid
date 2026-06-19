"""
Ecowitt collector - two ingestion paths into the `weather` bucket:

1. Webhook (preferred): the station's "Custom Server" feature POSTs an
   imperial-unit form payload every ~60s to /api/ecowitt/webhook.
2. API poller (fallback): polls the Ecowitt v3 real_time API every few minutes
   when ECOWITT_APP_KEY / ECOWITT_API_KEY / ECOWITT_MAC are configured.

Both paths normalise to the same metric field set (measurement: `station`).
"""
from __future__ import annotations

import asyncio
import logging
import math

import httpx
from fastapi import APIRouter, Request

import config
import db

log = logging.getLogger("ecowitt")
router = APIRouter()

ECOWITT_API_URL = "https://api.ecowitt.net/api/v3/device/real_time"


# ── Unit conversions (imperial -> metric) ──────────────────────────────────
def f_to_c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


def mph_to_kmh(mph: float) -> float:
    return mph * 1.609344


def inhg_to_hpa(inhg: float) -> float:
    return inhg * 33.8638866667


def inch_to_mm(inch: float) -> float:
    return inch * 25.4


def dewpoint_c(temp_c: float, rh: float) -> float | None:
    """Magnus-formula dew point from temperature (°C) and relative humidity (%)."""
    if rh <= 0:
        return None
    a, b = 17.625, 243.04
    gamma = math.log(rh / 100.0) + (a * temp_c) / (b + temp_c)
    return (b * gamma) / (a - gamma)


def apparent_temp_c(temp_c: float, rh: float, wind_kmh: float) -> float:
    """
    Australian Apparent Temperature ("feels like"). Works across the full range
    (heat + wind chill), unlike US heat index which is only valid when hot.
    """
    ws = wind_kmh / 3.6  # m/s
    vapor = (rh / 100.0) * 6.105 * math.exp(17.27 * temp_c / (237.7 + temp_c))
    return temp_c + 0.33 * vapor - 0.70 * ws - 4.00


# ── Helpers ────────────────────────────────────────────────────────────────
def _f(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_metric_fields(temp_c, hum, wind_kmh) -> dict:
    """Derive dew point + feels-like when the station does not supply them."""
    derived = {}
    if temp_c is not None and hum is not None:
        derived["dewpoint"] = round(dewpoint_c(temp_c, hum), 2)
    if temp_c is not None and hum is not None and wind_kmh is not None:
        derived["temperature_feels_like"] = round(
            apparent_temp_c(temp_c, hum, wind_kmh), 2
        )
    return derived


# ── Webhook path ───────────────────────────────────────────────────────────
def parse_webhook_form(form: dict) -> dict:
    """
    Convert an Ecowitt "Custom Server" form payload (imperial) to metric fields.
    Unknown / missing keys are simply skipped.
    """
    temp_c = _none_round(f_to_c, _f(form.get("tempf")))
    hum = _f(form.get("humidity"))
    wind_kmh = _none_round(mph_to_kmh, _f(form.get("windspeedmph")))
    gust_kmh = _none_round(mph_to_kmh, _f(form.get("windgustmph")))

    fields = {
        "temperature_outdoor": temp_c,
        "humidity_outdoor": hum,
        "temperature_indoor": _none_round(f_to_c, _f(form.get("tempinf"))),
        "humidity_indoor": _f(form.get("humidityin")),
        "wind_speed": wind_kmh,
        "wind_gust": gust_kmh,
        "wind_direction": _f(form.get("winddir")),
        "pressure_relative": _none_round(inhg_to_hpa, _f(form.get("baromrelin"))),
        "pressure_absolute": _none_round(inhg_to_hpa, _f(form.get("baromabsin"))),
        "rain_rate": _none_round(inch_to_mm, _f(form.get("rainratein"))),
        "rain_daily": _none_round(inch_to_mm, _f(form.get("dailyrainin"))),
        "solar_radiation": _f(form.get("solarradiation")),
        "uv_index": _f(form.get("uv")),
    }
    fields.update(_build_metric_fields(temp_c, hum, wind_kmh))
    return {k: v for k, v in fields.items() if v is not None}


def _none_round(fn, value, ndigits: int = 2):
    return round(fn(value), ndigits) if value is not None else None


@router.post("/api/ecowitt/webhook")
async def ecowitt_webhook(request: Request):
    """Receive a station push, normalise it and write it to InfluxDB."""
    form = dict((await request.form()))
    fields = parse_webhook_form(form)
    if not fields:
        log.warning("webhook payload had no usable fields: %s", list(form.keys()))
        return {"status": "ignored", "reason": "no usable fields"}

    station = form.get("PASSKEY") or form.get("stationtype") or "ecowitt"
    try:
        db.write_point(
            config.BUCKET_WEATHER,
            "station",
            fields,
            tags={"source": "webhook", "station": station[:24]},
        )
    except Exception as exc:  # noqa: BLE001
        log.error("influx write failed: %s", exc)
        return {"status": "error", "detail": str(exc)}

    log.info("webhook stored %d fields", len(fields))
    return {"status": "ok", "fields": len(fields)}


# ── API poller path ────────────────────────────────────────────────────────
def parse_api_response(data: dict) -> dict:
    """
    Parse the Ecowitt v3 real_time response (already requested in metric units).
    Each metric is shaped as {"time": ..., "unit": ..., "value": "12.3"}.
    """
    def val(group: str, key: str):
        node = (data.get(group) or {}).get(key)
        return _f(node.get("value")) if isinstance(node, dict) else None

    rain_group = "rainfall" if "rainfall" in data else "rainfall_piezo"
    temp_c = val("outdoor", "temperature")
    hum = val("outdoor", "humidity")
    wind_kmh = val("wind", "wind_speed")

    fields = {
        "temperature_outdoor": temp_c,
        "humidity_outdoor": hum,
        "temperature_indoor": val("indoor", "temperature"),
        "humidity_indoor": val("indoor", "humidity"),
        "temperature_feels_like": val("outdoor", "feels_like"),
        "dewpoint": val("outdoor", "dew_point"),
        "wind_speed": wind_kmh,
        "wind_gust": val("wind", "wind_gust"),
        "wind_direction": val("wind", "wind_direction"),
        "pressure_relative": val("pressure", "relative"),
        "pressure_absolute": val("pressure", "absolute"),
        "rain_rate": val(rain_group, "rain_rate"),
        "rain_daily": val(rain_group, "daily"),
        "solar_radiation": val("solar_and_uvi", "solar"),
        "uv_index": val("solar_and_uvi", "uvi"),
    }
    # Fill derived fields only where the API left a gap.
    for key, value in _build_metric_fields(temp_c, hum, wind_kmh).items():
        fields.setdefault(key, value)
        if fields.get(key) is None:
            fields[key] = value
    return {k: v for k, v in fields.items() if v is not None}


async def poll_once(client: httpx.AsyncClient) -> bool:
    params = {
        "application_key": config.ECOWITT_APP_KEY,
        "api_key": config.ECOWITT_API_KEY,
        "mac": config.ECOWITT_MAC,
        "call_back": "all",
        "temp_unitid": 1,        # Celsius
        "pressure_unitid": 3,    # hPa
        "wind_speed_unitid": 7,  # km/h
        "rainfall_unitid": 12,   # mm
        "solar_irradiance_unitid": 16,  # W/m²
    }
    resp = await client.get(ECOWITT_API_URL, params=params, timeout=20)
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 0:
        log.warning("ecowitt api error: %s", body.get("msg"))
        return False

    fields = parse_api_response(body.get("data") or {})
    if not fields:
        return False
    db.write_point(
        config.BUCKET_WEATHER,
        "station",
        fields,
        tags={"source": "api", "station": config.ECOWITT_MAC},
    )
    log.info("api poll stored %d fields", len(fields))
    return True


async def run_poller() -> None:
    """Background fallback poller. No-op when API keys are not configured."""
    if not config.ecowitt_enabled():
        log.info("ecowitt API poller disabled (no keys) - webhook still active")
        return
    log.info("ecowitt API poller started (every %ds)", config.ECOWITT_POLL_INTERVAL)
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await poll_once(client)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("ecowitt poll failed: %s", exc)
            await asyncio.sleep(config.ECOWITT_POLL_INTERVAL)
