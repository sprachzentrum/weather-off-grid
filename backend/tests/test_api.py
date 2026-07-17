"""API-level tests: admin auth, unknown sites, webhook auth, secret masking.

The app is assembled from the routers directly (no main.py lifecycle) and all
InfluxDB access is monkeypatched, so no external services are needed.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api as api_module
import config
import db
import security
import settings_api
import settings_store
from collectors import ecowitt_collector
from conftest import make_site, make_state

PIN = "1234"
MAC = "C4:5B:BE:6E:46:15"


@pytest.fixture
def client(temp_settings, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_PIN", PIN)

    async def _noop_reload():
        return None

    monkeypatch.setattr(settings_api.manager, "reload", _noop_reload)
    settings_store.save_state(make_state([
        make_site("el-durazno",
                  ecowitt={"enabled": True, "mac": MAC, "api_key": "api-secret",
                           "app_key": "app-secret"},
                  growatt={"enabled": True, "username": "user", "password": "gw-secret"}),
    ]))

    app = FastAPI()
    app.include_router(ecowitt_collector.router)
    app.include_router(api_module.router)
    app.include_router(settings_api.router)
    return TestClient(app)


# ── admin PIN ────────────────────────────────────────────────────────────────
def test_settings_requires_pin(client):
    assert client.get("/api/settings").status_code == 401


def test_settings_with_header_pin(client):
    r = client.get("/api/settings", headers={"X-Admin-Pin": PIN})
    assert r.status_code == 200


def test_pin_as_query_param_rejected(client):
    """PINs in URLs leak into logs/history - the query fallback must be gone."""
    assert client.get(f"/api/settings?pin={PIN}").status_code == 401


def test_pin_brute_force_locked_out(client):
    for _ in range(security.limiter.MAX_FAILURES):
        assert client.get("/api/settings", headers={"X-Admin-Pin": "wrong"}).status_code == 401
    # Locked now - even the correct PIN is refused with 429.
    assert client.get("/api/settings", headers={"X-Admin-Pin": PIN}).status_code == 429


def test_verify_pin_endpoint(client):
    ok = client.post("/api/settings/verify-pin", json={"pin": PIN}).json()
    assert ok == {"ok": True, "required": True}
    bad = client.post("/api/settings/verify-pin", json={"pin": "nope"}).json()
    assert bad["ok"] is False


# ── secret masking over the API ──────────────────────────────────────────────
def test_get_settings_masks_secrets(client):
    site = client.get("/api/settings", headers={"X-Admin-Pin": PIN}).json()["sites"][0]
    assert site["growatt"]["password"] == settings_store.MASK
    assert site["ecowitt"]["api_key"] == settings_store.MASK
    assert site["ecowitt"]["app_key"] == settings_store.MASK


def test_post_settings_keeps_masked_secrets(client):
    masked = client.get("/api/settings", headers={"X-Admin-Pin": PIN}).json()
    r = client.post("/api/settings", json={"sites": masked["sites"]},
                    headers={"X-Admin-Pin": PIN})
    assert r.status_code == 200
    stored = settings_store.get_site("el-durazno")
    assert stored["growatt"]["password"] == "gw-secret"
    assert stored["ecowitt"]["api_key"] == "api-secret"


def test_post_settings_rejects_invalid_values(client):
    site = make_site("el-durazno", latitude=999)
    r = client.post("/api/settings", json={"sites": [site]},
                    headers={"X-Admin-Pin": PIN})
    assert r.status_code == 400
    assert "latitude" in r.json()["detail"]


def test_frontend_config_has_no_secrets(client):
    r = client.get("/api/settings/frontend")
    assert r.status_code == 200
    text = r.text
    assert "gw-secret" not in text
    assert "api-secret" not in text
    assert "app-secret" not in text


# ── unknown site -> 404 ──────────────────────────────────────────────────────
def test_unknown_site_is_404(client, monkeypatch):
    monkeypatch.setattr(db, "latest_fields", lambda *a, **k: {})
    r = client.get("/api/current?site=does-not-exist")
    assert r.status_code == 404
    assert r.json()["detail"] == "site not found"


# ── ecowitt webhook auth ─────────────────────────────────────────────────────
def test_webhook_accepts_known_station(client, monkeypatch):
    written = {}

    def fake_write(bucket, measurement, fields, tags=None, ts=None):
        written.update({"fields": fields, "tags": tags})

    monkeypatch.setattr(db, "write_point", fake_write)
    r = client.post("/api/ecowitt/webhook",
                    data={"MAC": MAC, "tempf": "68.0", "humidity": "50"})
    assert r.status_code == 200
    assert r.json()["site_id"] == "el-durazno"
    assert written["tags"]["site_id"] == "el-durazno"
    assert written["fields"]["temperature_outdoor"] == 20.0


def test_webhook_rejects_unknown_station(client, monkeypatch):
    monkeypatch.setattr(db, "write_point",
                        lambda *a, **k: pytest.fail("must not write for unknown station"))
    r = client.post("/api/ecowitt/webhook",
                    data={"MAC": "00:11:22:33:44:55", "tempf": "68.0"})
    assert r.status_code == 401


def test_webhook_hides_internal_errors(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("influx exploded at 10.0.0.5:8086 token=abc")

    monkeypatch.setattr(db, "write_point", boom)
    r = client.post("/api/ecowitt/webhook", data={"MAC": MAC, "tempf": "68.0"})
    assert r.status_code == 503
    assert "10.0.0.5" not in r.text
    assert "token" not in r.text


# ── reports parameter validation ─────────────────────────────────────────────
def test_reports_latest_validates_period(client):
    r = client.get("/api/reports/latest?period=../../etc/passwd")
    assert r.status_code == 400
