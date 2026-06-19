"""
Settings / admin API.

  GET    /api/settings            read settings (secrets masked)      [PIN]
  POST   /api/settings            bulk update + hot-reload collectors [PIN]
  GET    /api/settings/status     live status of every service        [PIN]
  POST   /api/settings/test       test one service connection         [PIN]
  GET    /api/settings/frontend   public config for the PWA (no PIN)
  POST   /api/settings/verify-pin check a PIN                          (no PIN)
  POST   /api/sites               add / update one site               [PIN]
  DELETE /api/sites/{site_id}     remove a site                       [PIN]

[PIN]: protected by ADMIN_PIN when set. If ADMIN_PIN is empty, the endpoints are
open (acceptable on a trusted local network, as documented).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query

import api
import config
import db
import settings_store
from collector_manager import manager
from collectors import ecowitt_collector, growatt_collector
from forecast import microclimate

log = logging.getLogger("settings_api")
router = APIRouter(prefix="/api")


# ── PIN protection ─────────────────────────────────────────────────────────
def _pin_ok(pin: str | None) -> bool:
    if not config.ADMIN_PIN:
        return True
    return pin == config.ADMIN_PIN


async def require_pin(
    x_admin_pin: str | None = Header(default=None),
    pin: str | None = Query(default=None),
) -> None:
    if not _pin_ok(x_admin_pin or pin):
        raise HTTPException(status_code=401, detail="invalid or missing admin PIN")


async def _after_change() -> None:
    """Invalidate caches and hot-reload collectors after a settings change."""
    api.invalidate_cache()
    microclimate.invalidate()
    await manager.reload()


# ── public ─────────────────────────────────────────────────────────────────
@router.get("/settings/frontend")
async def settings_frontend():
    """Public config the PWA can load instead of config.js (no secrets, no PIN)."""
    return settings_store.frontend_config()


@router.post("/settings/verify-pin")
async def verify_pin(payload: dict = Body(default={})):
    return {"ok": _pin_ok(payload.get("pin")), "required": bool(config.ADMIN_PIN)}


# ── protected ──────────────────────────────────────────────────────────────
@router.get("/settings", dependencies=[Depends(require_pin)])
async def get_settings():
    return settings_store.public_settings()


@router.post("/settings", dependencies=[Depends(require_pin)])
async def post_settings(payload: dict = Body(...)):
    result = settings_store.apply_update(payload)
    await _after_change()
    return result


@router.post("/sites", dependencies=[Depends(require_pin)])
async def post_site(site: dict = Body(...)):
    saved = settings_store.upsert_site(site)
    await _after_change()
    return settings_store._mask_site(saved)


@router.delete("/sites/{site_id}", dependencies=[Depends(require_pin)])
async def delete_site(site_id: str):
    ok = settings_store.delete_site(site_id)
    if not ok:
        raise HTTPException(status_code=404, detail="site not found")
    await _after_change()
    return {"deleted": site_id}


@router.get("/settings/status", dependencies=[Depends(require_pin)])
async def settings_status():
    """Live status: InfluxDB health + datapoint counts + per-site last-seen."""
    influx_ok = db.health()
    buckets = {
        "weather": db.count_points(config.BUCKET_WEATHER) if influx_ok else 0,
        "energy": db.count_points(config.BUCKET_ENERGY) if influx_ok else 0,
        "forecasts": db.count_points(config.BUCKET_FORECASTS) if influx_ok else 0,
    }
    sites_status = []
    for s in settings_store.sites():
        sid = s["site_id"]
        eco = s.get("ecowitt") or {}
        gw = s.get("growatt") or {}
        weather_seen = db.latest_fields(config.BUCKET_WEATHER, "station", site_id=sid).get("_time") if influx_ok else None
        energy_seen = db.latest_fields(config.BUCKET_ENERGY, "energy", site_id=sid).get("_time") if influx_ok else None
        sites_status.append({
            "site_id": sid,
            "name": s.get("name"),
            "ecowitt": {"enabled": bool(eco.get("enabled")), "last_seen": _iso(weather_seen)},
            "growatt": {"enabled": bool(gw.get("enabled")), "last_seen": _iso(energy_seen)},
            "weather_points": db.count_points(config.BUCKET_WEATHER, site_id=sid) if influx_ok else 0,
            "energy_points": db.count_points(config.BUCKET_ENERGY, site_id=sid) if influx_ok else 0,
        })
    return {
        "influxdb": {"ok": influx_ok, "url": config.INFLUXDB_URL, "buckets": buckets},
        "sites": sites_status,
    }


@router.post("/settings/test", dependencies=[Depends(require_pin)])
async def settings_test(payload: dict = Body(...)):
    """
    Test a service connection. Body: {service, site_id?, site?}.
    If a `site` object with credentials is supplied, those (unsaved) values are
    tested - so the setup wizard can validate before saving. Otherwise the stored
    site identified by site_id (or the default) is used.
    """
    service = (payload.get("service") or "").lower()
    site = payload.get("site")
    if not isinstance(site, dict):
        site = settings_store.get_site(payload.get("site_id")) or {}
    # Ensure nested groups exist so collectors can read them.
    site = {"site_id": site.get("site_id", "test"), **site}
    site.setdefault("ecowitt", {})
    site.setdefault("growatt", {})

    if service == "ecowitt":
        return await ecowitt_collector.test_connection(site)
    if service == "growatt":
        return await growatt_collector.test_connection(site)
    if service == "influxdb":
        ok = db.health()
        return {"ok": ok, "detail": "InfluxDB erreichbar" if ok else "InfluxDB nicht erreichbar"}
    raise HTTPException(status_code=400, detail="unknown service (ecowitt|growatt|influxdb)")


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else None
