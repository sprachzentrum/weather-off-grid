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
import time

import httpx
from fastapi import APIRouter, HTTPException, Request

import collector_status
import config
import db
import settings_store

log = logging.getLogger("ecowitt")
router = APIRouter()

ECOWITT_API_URL = "https://api.ecowitt.net/api/v3/device/real_time"

# Plausible physical ranges per metric field. Values outside are dropped so a
# corrupt (or malicious) payload cannot poison charts, the microclimate model
# or the fire-danger index.
PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    "temperature_outdoor": (-60.0, 65.0),
    "temperature_indoor": (-60.0, 65.0),
    "temperature_feels_like": (-90.0, 80.0),
    "dewpoint": (-90.0, 65.0),
    "humidity_outdoor": (0.0, 100.0),
    "humidity_indoor": (0.0, 100.0),
    "wind_speed": (0.0, 300.0),
    "wind_gust": (0.0, 350.0),
    "wind_direction": (0.0, 360.0),
    "pressure_relative": (800.0, 1100.0),
    "pressure_absolute": (500.0, 1100.0),
    "rain_rate": (0.0, 500.0),
    "rain_daily": (0.0, 1000.0),
    "solar_radiation": (0.0, 1700.0),
    "uv_index": (0.0, 25.0),
}


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
    return {
        k: v for k, v in fields.items()
        if v is not None and _in_range(k, v)
    }


def _in_range(field: str, value: float) -> bool:
    lo, hi = PLAUSIBLE_RANGES.get(field, (float("-inf"), float("inf")))
    return lo <= value <= hi


def _none_round(fn, value, ndigits: int = 2):
    return round(fn(value), ndigits) if value is not None else None


# Throttle for rejected-webhook log lines: one per station identifier / minute,
# so an unauthenticated flood cannot fill the logs.
_reject_log_at: dict[str, float] = {}


def _log_rejected(mac, client: str) -> None:
    key = str(mac)[:24] or "?"
    now = time.monotonic()
    if now - _reject_log_at.get(key, float("-inf")) >= 60:
        _reject_log_at[key] = now
        log.warning("webhook rejected: unknown station mac=%s from %s", key, client)


@router.post("/api/ecowitt/webhook")
async def ecowitt_webhook(request: Request):
    """Receive a station push, route it to a site by MAC, write it to InfluxDB.

    Payloads whose MAC/PASSKEY matches no configured station are rejected with
    401 (see settings_store.site_for_ecowitt for the documented first-run
    exception), so arbitrary clients cannot inject weather data.
    """
    form = dict((await request.form()))

    # Identify which site this station belongs to (by MAC, else PASSKEY hash).
    mac = form.get("MAC") or form.get("mac") or form.get("ID")
    site = settings_store.site_for_ecowitt(mac, form.get("PASSKEY"))
    if site is None:
        _log_rejected(mac, request.client.host if request.client else "?")
        raise HTTPException(status_code=401, detail="unknown station")

    fields = parse_webhook_form(form)
    if not fields:
        log.warning("webhook payload had no usable fields: %s", list(form.keys()))
        return {"status": "ignored", "reason": "no usable fields"}

    site_id = site["site_id"]
    station = form.get("PASSKEY") or form.get("stationtype") or mac or "ecowitt"
    try:
        db.write_point(
            config.BUCKET_WEATHER,
            "station",
            fields,
            tags={"source": "webhook", "station": str(station)[:24], "site_id": site_id},
        )
    except Exception as exc:  # noqa: BLE001
        # Log the details server-side only; no internals in the response.
        log.error("influx write failed: %s", exc)
        collector_status.record_error(site_id, "webhook", exc)
        raise HTTPException(status_code=503, detail="storage unavailable")

    collector_status.record_success(site_id, "webhook")
    log.info("webhook stored %d fields for site=%s", len(fields), site_id)
    return {"status": "ok", "fields": len(fields), "site_id": site_id}


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


def _ecowitt_params(eco: dict) -> dict:
    return {
        "application_key": eco.get("app_key"),
        "api_key": eco.get("api_key"),
        "mac": eco.get("mac"),
        "call_back": "all",
        "temp_unitid": 1,        # Celsius
        "pressure_unitid": 3,    # hPa
        "wind_speed_unitid": 7,  # km/h
        "rainfall_unitid": 12,   # mm
        "solar_irradiance_unitid": 16,  # W/m²
    }


def _ecowitt_ready(site: dict) -> bool:
    eco = site.get("ecowitt") or {}
    return bool(eco.get("enabled") and eco.get("app_key") and eco.get("api_key") and eco.get("mac"))


async def poll_once(client: httpx.AsyncClient, site: dict) -> bool:
    eco = site.get("ecowitt") or {}
    resp = await client.get(ECOWITT_API_URL, params=_ecowitt_params(eco), timeout=20)
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 0:
        log.warning("[%s] ecowitt api error: %s", site["site_id"], body.get("msg"))
        return False

    fields = parse_api_response(body.get("data") or {})
    if not fields:
        return False
    db.write_point(
        config.BUCKET_WEATHER,
        "station",
        fields,
        tags={"source": "api", "station": eco.get("mac", ""), "site_id": site["site_id"]},
    )
    log.info("[%s] api poll stored %d fields", site["site_id"], len(fields))
    return True


async def test_connection(site: dict) -> dict:
    """Live connectivity check used by POST /api/settings/test."""
    if not _ecowitt_ready(site):
        return {"ok": False, "detail": "Ecowitt nicht konfiguriert (Keys/MAC fehlen)"}
    eco = site.get("ecowitt") or {}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(ECOWITT_API_URL, params=_ecowitt_params(eco), timeout=15)
            resp.raise_for_status()
            body = resp.json()
        if body.get("code") != 0:
            return {"ok": False, "detail": f"Ecowitt: {body.get('msg')}"}
        fields = parse_api_response(body.get("data") or {})
        return {"ok": True, "detail": f"OK - {len(fields)} Messwerte empfangen"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)}


async def run_poller(site: dict) -> None:
    """Per-site background fallback poller. No-op when not configured."""
    sid = site["site_id"]
    if not _ecowitt_ready(site):
        log.info("[%s] ecowitt API poller disabled (webhook still active)", sid)
        return
    log.info("[%s] ecowitt API poller started (every %ds)", sid, config.ECOWITT_POLL_INTERVAL)
    async with httpx.AsyncClient() as client:
        while True:
            try:
                if await poll_once(client, site):
                    collector_status.record_success(sid, "ecowitt_api")
                else:
                    collector_status.record_error(sid, "ecowitt_api", "no data (see log)")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("[%s] ecowitt poll failed: %s", sid, exc)
                collector_status.record_error(sid, "ecowitt_api", exc)
            await asyncio.sleep(config.ECOWITT_POLL_INTERVAL)
