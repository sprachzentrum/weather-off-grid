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

[PIN]: protected by the admin PIN (see security.py). The PIN is accepted only
via the X-Admin-Pin header - never as a query parameter, because URLs end up in
browser history, proxies and logs. Failed attempts are rate-limited per client.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request

import api
import collector_status
import config
import db
import security
import settings_store
from collector_manager import manager
from collectors import ecowitt_collector, growatt_collector
from forecast import microclimate

log = logging.getLogger("settings_api")
router = APIRouter(prefix="/api")


# ── PIN protection ─────────────────────────────────────────────────────────
def _client_id(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_rate_limited(request: Request, pin: str | None) -> bool:
    """Rate-limited PIN check shared by require_pin and /settings/verify-pin."""
    if not security.pin_required():
        return True
    client = _client_id(request)
    if security.limiter.blocked(client):
        raise HTTPException(status_code=429,
                            detail="too many failed attempts - try again later")
    if security.check_pin(pin):
        security.limiter.register_success(client)
        return True
    security.limiter.register_failure(client)
    return False


async def require_pin(
    request: Request,
    x_admin_pin: str | None = Header(default=None),
) -> None:
    if not _check_rate_limited(request, x_admin_pin):
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
async def verify_pin(request: Request, payload: dict = Body(default={})):
    ok = _check_rate_limited(request, payload.get("pin"))
    return {"ok": ok, "required": security.pin_required()}


# ── protected ──────────────────────────────────────────────────────────────
@router.get("/settings", dependencies=[Depends(require_pin)])
async def get_settings():
    return settings_store.public_settings()


@router.post("/settings", dependencies=[Depends(require_pin)])
async def post_settings(payload: dict = Body(...)):
    try:
        result = settings_store.apply_update(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await _after_change()
    return result


@router.post("/sites", dependencies=[Depends(require_pin)])
async def post_site(site: dict = Body(...)):
    try:
        saved = settings_store.upsert_site(site)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await _after_change()
    return settings_store.mask_site(saved)


@router.get("/settings/backup", dependencies=[Depends(require_pin)])
async def settings_backup():
    """Download the full settings (incl. secrets) as a restorable JSON backup."""
    from fastapi.responses import JSONResponse

    return JSONResponse(
        settings_store.backup_state(),
        headers={"Content-Disposition":
                 'attachment; filename="weather-off-grid-settings.json"'},
    )


@router.post("/settings/restore", dependencies=[Depends(require_pin)])
async def settings_restore(payload: dict = Body(...)):
    """Replace the settings from an uploaded backup (validated before saving)."""
    try:
        result = settings_store.restore_state(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await _after_change()
    return result


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
            # Per-collector health: last successful poll/delivery + last error.
            "collectors": collector_status.for_site(sid),
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
    else:
        # The form round-trips masked secrets ("********"); swap them back for
        # the stored values so a connection test can run without re-typing them.
        stored = settings_store.get_site(site.get("site_id")) if site.get("site_id") else None
        site = settings_store.unmask_site(site, stored)
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


@router.post("/reports/test", dependencies=[Depends(require_pin)])
async def reports_test(payload: dict = Body(default={})):
    """
    Generate and e-mail a test report immediately. Body: {site_id?, period?,
    email_to?}. Falls back to the stored reports config / default site.
    """
    from reports import generator as reports_gen

    cfg = settings_store.reports_config()
    period = payload.get("period") or cfg.get("schedule") or "weekly"
    if period not in ("weekly", "monthly"):
        period = "weekly"
    recipient = payload.get("email_to") or cfg.get("email_to") or config.REPORT_EMAIL_TO
    if not config.smtp_enabled():
        return {"ok": False, "detail": "SMTP nicht konfiguriert (.env: SMTP_HOST, REPORT_EMAIL_FROM)"}
    if not recipient:
        return {"ok": False, "detail": "Keine Empfänger-E-Mail eingetragen"}
    site = settings_store.get_site(payload.get("site_id"))
    if site is None:
        return {"ok": False, "detail": "Kein Standort konfiguriert"}
    try:
        await asyncio.to_thread(reports_gen.generate_and_send, site["site_id"], period, recipient)
        return {"ok": True, "detail": f"Testbericht gesendet an {recipient}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)}


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else None
