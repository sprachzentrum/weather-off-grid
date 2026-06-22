"""
Night-summary collector - persists per-night battery consumption as its own
time series so the trend can be charted and reported over weeks/months.

The dashboard (/energy/autonomy) and the PDF report derive a *live* night
consumption from the SOC curve every time they are called, but nothing was
stored, so there was no per-night history. This collector records one datapoint
per completed night (measurement `night_summary` in the energy bucket):

    consumption_kwh, power_w, hours, soc_sunset, soc_sunrise   (tag: site_id)

The point's timestamp is that night's sunrise (UTC), which is deterministic, so
re-running the backfill simply overwrites the same point - InfluxDB dedupes by
(measurement, tag set, timestamp). That makes the collector naturally
idempotent: each run rewrites the last few nights, refining values as more SOC
history arrives, without ever creating duplicates.

Requires Growatt (the SOC source); for weather-only sites it does nothing.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

import config
import db
from collectors import openmeteo_collector as om

log = logging.getLogger("night")

# How many trailing nights to (re)compute and persist on each run. Generous so a
# site that was offline for a few days backfills once it comes back.
BACKFILL_NIGHTS = 8


def _enabled(site: dict) -> bool:
    gw = site.get("growatt") or {}
    return bool(gw.get("enabled") and gw.get("username") and gw.get("password"))


async def _persist_once(client: httpx.AsyncClient, site: dict) -> int:
    """Compute completed nights from the SOC history and persist them. Returns
    the number of nights written/updated."""
    # Imported lazily to avoid any import-order coupling with the API module.
    import api

    sid = site["site_id"]
    capacity = site.get("battery_capacity_kwh", config.BATTERY_CAPACITY_KWH)
    raw = await om.fetch_forecast(
        client, site.get("latitude"), site.get("longitude"), site.get("timezone")
    )
    daily = raw.get("daily") or {}
    tz = api._site_tz(site)
    socs = api._soc_series(site, days=BACKFILL_NIGHTS + 2)
    now_utc = datetime.now(timezone.utc)

    nights = api.compute_nights(socs, daily, tz, capacity, now_utc)[-BACKFILL_NIGHTS:]
    written = 0
    for n in nights:
        db.write_point(
            config.BUCKET_ENERGY,
            "night_summary",
            {
                "consumption_kwh": round(n["kwh"], 3),
                "power_w": round(n["power_w"], 1),
                "hours": round(n["hours"], 2),
                "soc_sunset": round(n["soc_sunset"], 1),
                "soc_sunrise": round(n["soc_sunrise"], 1),
            },
            tags={"site_id": sid},
            ts=n["sunrise_utc"],
        )
        written += 1
    if written:
        log.info("[%s] night summary: stored/updated %d night(s)", sid, written)
    return written


async def run_poller(site: dict) -> None:
    sid = site["site_id"]
    if not _enabled(site):
        log.info("[%s] night summary disabled (no Growatt/SOC) - skipping", sid)
        return
    log.info("[%s] night summary collector started (every %ds)", sid, config.NIGHT_SUMMARY_INTERVAL)
    client = httpx.AsyncClient()
    try:
        while True:
            try:
                await _persist_once(client, site)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("[%s] night summary failed: %s", sid, exc)
            await asyncio.sleep(config.NIGHT_SUMMARY_INTERVAL)
    finally:
        await client.aclose()
