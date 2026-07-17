"""
Weather Off-Grid - FastAPI backend.

Starts the data collectors as background tasks on startup, bootstraps the
InfluxDB buckets, and exposes the REST API consumed by the PWA frontend.

The full set of API endpoints lives in api.py and is mounted below; this module
owns the application lifecycle (startup/shutdown) and the background task pool.
"""
from __future__ import annotations

import asyncio
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import api
import config
import db
import security
import settings_store
from collectors import ecowitt_collector
from collector_manager import manager
from reports import generator as report_generator
from api import router as api_router
from settings_api import router as settings_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("main")


async def _startup() -> None:
    log.info("starting Weather Off-Grid backend")

    # Resolve the admin PIN early: with no ADMIN_PIN configured this generates
    # one and persists it to DATA_DIR/admin_pin.txt (never left silently open).
    if security.pin_required():
        log.info("admin API is PIN-protected")
    else:
        log.warning("admin API runs WITHOUT a PIN (ADMIN_ALLOW_OPEN=true)")

    # Bootstrap InfluxDB buckets (idempotent). Retry briefly so we tolerate
    # InfluxDB still coming up in the docker-compose network.
    for attempt in range(1, 11):
        if db.health():
            break
        log.info("waiting for InfluxDB (%d/10) ...", attempt)
        await asyncio.sleep(3)
    try:
        db.ensure_buckets()
    except Exception as exc:  # noqa: BLE001
        log.error("bucket bootstrap failed: %s", exc)

    # Load settings (from settings.json or bootstrapped from .env) and start one
    # set of collectors per configured site.
    settings_store.load()
    await manager.reload()

    # Background report scheduler (weekly/monthly PDF e-mails). One global task.
    global _report_task
    _report_task = asyncio.create_task(report_generator.run_scheduler())
    log.info("backend ready (%d site(s) configured)", len(settings_store.sites()))


_report_task: asyncio.Task | None = None


async def _shutdown() -> None:
    await manager.stop()
    if _report_task is not None:
        _report_task.cancel()
        try:
            await _report_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            log.warning("report scheduler ended with error: %s", exc)
    await api.aclose_client()
    db.close()
    log.info("backend stopped")


app = FastAPI(
    title="Weather Off-Grid",
    description="Weather + energy dashboard with microclimate forecast correction.",
    version="1.0.0",
    on_startup=[_startup],
    on_shutdown=[_shutdown],
)

# CORS: only origins explicitly listed in CORS_ORIGINS may call the API from a
# browser. Empty (default) adds no CORS headers at all - correct when the
# backend serves the PWA itself. Never fall back to "*" implicitly: combined
# with an unset PIN that would let any website reconfigure a LAN-reachable
# backend.
if config.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-Admin-Pin"],
    )
    log.info("CORS enabled for: %s", ", ".join(config.CORS_ORIGINS))

# Ecowitt webhook + data API + settings/admin API.
app.include_router(ecowitt_collector.router)
app.include_router(api_router)
app.include_router(settings_router)


@app.get("/health")
async def health() -> JSONResponse:
    """Liveness + InfluxDB readiness in one call."""
    influx_ok = db.health()
    return JSONResponse(
        status_code=200 if influx_ok else 503,
        content={
            "status": "ok" if influx_ok else "degraded",
            "influxdb": influx_ok,
            "collectors": {
                "ecowitt_api": config.ecowitt_enabled(),
                "growatt": config.growatt_enabled(),
                "openmeteo": True,
            },
        },
    )


# Optionally serve the PWA from the same container (docker-compose mounts
# ./frontend to /app/static). Mounted last so it never shadows /api or /health.
_STATIC_DIR = os.environ.get("STATIC_DIR", "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")
    log.info("serving frontend from %s", _STATIC_DIR)

