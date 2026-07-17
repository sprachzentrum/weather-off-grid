"""Shared fixtures: isolated settings store + admin-PIN state per test."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import config  # noqa: E402
import security  # noqa: E402
import settings_store  # noqa: E402


@pytest.fixture
def temp_settings(tmp_path, monkeypatch):
    """Point DATA_DIR / settings.json at a temp dir and reset cached state."""
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    settings_store.reset_for_test()
    security.reset_for_test()
    security.limiter.reset()
    yield tmp_path
    settings_store.reset_for_test()
    security.reset_for_test()
    security.limiter.reset()


def make_state(sites: list[dict]) -> dict:
    """Minimal persisted-settings state around a list of site dicts."""
    return {
        "version": 1,
        "default_site": sites[0]["site_id"] if sites else "none",
        "display": {"language": "de", "units": "metric", "refresh_interval": 300},
        "reports": {"enabled": False, "email_to": "", "schedule": "weekly"},
        "sites": sites,
    }


def make_site(site_id: str = "el-durazno", **overrides) -> dict:
    site = {
        "site_id": site_id,
        "name": site_id,
        "latitude": -32.15,
        "longitude": -64.79,
        "altitude": 1000,
        "timezone": "America/Argentina/Cordoba",
        "ecowitt": {"enabled": False, "app_key": "", "api_key": "", "mac": ""},
        "growatt": {"enabled": False, "username": "", "password": "",
                    "plant_id": "", "inverter_sn": "",
                    "server_url": "https://server.growatt.com"},
        "pv_kwp": 3.6,
        "pv_efficiency": 0.75,
        "battery_capacity_kwh": 9.6,
        "wind_threshold_kmh": 10,
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(site.get(key), dict):
            site[key].update(value)
        else:
            site[key] = value
    return site
