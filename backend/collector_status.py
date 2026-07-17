"""
In-memory collector status registry.

Collectors report every successful poll / delivery and every failure here, so
the settings page can show per-site, per-collector health: when data last
arrived and what the last error was. Deliberately in-memory only - after a
restart the registry refills within one poll cycle.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

_lock = threading.Lock()
# site_id -> collector name -> {last_success, last_error, error}
_status: dict[str, dict[str, dict]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_success(site_id: str, collector: str) -> None:
    with _lock:
        info = _status.setdefault(site_id, {}).setdefault(collector, {})
        info["last_success"] = _now()
        info["error"] = None


def record_error(site_id: str, collector: str, error) -> None:
    with _lock:
        info = _status.setdefault(site_id, {}).setdefault(collector, {})
        info["last_error"] = _now()
        info["error"] = str(error)[:200]


def for_site(site_id: str) -> dict[str, dict]:
    with _lock:
        return {name: dict(info) for name, info in _status.get(site_id, {}).items()}


def reset_for_test() -> None:
    with _lock:
        _status.clear()
