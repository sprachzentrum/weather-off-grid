"""
Collector lifecycle manager.

Runs one Ecowitt poller, one Growatt poller and one Open-Meteo archiver per
configured site as asyncio tasks. On a settings change the API calls reload(),
which cancels every running task and re-spawns the set from the current site
list - so adding/removing a site or changing credentials takes effect without a
server restart.
"""
from __future__ import annotations

import asyncio
import logging

import settings_store
from collectors import (
    ecowitt_collector,
    growatt_collector,
    night_collector,
    openmeteo_collector,
)

log = logging.getLogger("manager")


class CollectorManager:
    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []

    async def _cancel_all(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks = []

    async def reload(self) -> None:
        """Restart all collectors from the current site configuration."""
        await self._cancel_all()
        sites = settings_store.sites()
        for site in sites:
            self._tasks.append(asyncio.create_task(ecowitt_collector.run_poller(site)))
            self._tasks.append(asyncio.create_task(growatt_collector.run_poller(site)))
            self._tasks.append(asyncio.create_task(openmeteo_collector.run_poller(site)))
            self._tasks.append(asyncio.create_task(night_collector.run_poller(site)))
        log.info("collectors (re)started for %d site(s): %s",
                 len(sites), ", ".join(s["site_id"] for s in sites) or "-")

    async def stop(self) -> None:
        await self._cancel_all()


# Process-wide singleton used by main.py (lifecycle) and settings_api (reload).
manager = CollectorManager()
