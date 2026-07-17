"""API tests for the phase-5 features: alerts endpoint, CSV export,
settings backup/restore, collector status."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api as api_module
import collector_status
import config
import db
import settings_api
import settings_store
from collectors import ecowitt_collector
from conftest import make_site, make_state

PIN = "1234"
HDR = {"X-Admin-Pin": PIN}


@pytest.fixture
def client(temp_settings, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_PIN", PIN)

    async def _noop_reload():
        return None

    monkeypatch.setattr(settings_api.manager, "reload", _noop_reload)
    collector_status.reset_for_test()
    settings_store.save_state(make_state([
        make_site("el-durazno",
                  growatt={"enabled": True, "username": "user", "password": "gw-secret"},
                  alerts={"enabled": True, "soc_min": 30}),
    ]))

    app = FastAPI()
    app.include_router(ecowitt_collector.router)
    app.include_router(api_module.router)
    app.include_router(settings_api.router)
    return TestClient(app)


# ── /api/alerts ──────────────────────────────────────────────────────────────
def test_alerts_endpoint(client, monkeypatch):
    def fake_latest(bucket, measurement, site_id=None, since="-2h"):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        if measurement == "energy":
            return {"battery_soc": 12.0, "_time": now}
        return {"temperature_outdoor": 22.0, "wind_speed": 10.0, "_time": now}

    monkeypatch.setattr(db, "latest_fields", fake_latest)
    monkeypatch.setattr("forecast.fire_danger.days_since_rain", lambda sid, today=None: 0)
    r = client.get("/api/alerts")
    assert r.status_code == 200
    body = r.json()
    assert [a["key"] for a in body["alerts"]] == ["soc"]
    assert body["thresholds"]["soc_min"] == 30


def test_alerts_unknown_site_404(client):
    assert client.get("/api/alerts?site=nope").status_code == 404


# ── /api/export/csv ──────────────────────────────────────────────────────────
def test_export_csv(client, monkeypatch):
    def fake_series(bucket, measurement, fields, site_id=None, days=7, every="1h"):
        return {"time": ["2026-07-16T00:00:00Z", "2026-07-16T01:00:00Z"],
                **{f: [1.5, None] for f in fields}}

    monkeypatch.setattr(db, "series", fake_series)
    r = client.get("/api/export/csv?bucket=energy&days=7")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("time,battery_soc")
    assert lines[1].startswith("2026-07-16T00:00:00Z,1.5")
    assert lines[2].endswith(",,,,")  # None -> empty cells


def test_export_csv_validates_params(client):
    assert client.get("/api/export/csv?bucket=nope").status_code == 400
    assert client.get("/api/export/csv?every=13m").status_code == 400
    assert client.get("/api/export/csv?days=9999").status_code == 422


# ── backup / restore ─────────────────────────────────────────────────────────
def test_backup_requires_pin(client):
    assert client.get("/api/settings/backup").status_code == 401


def test_backup_restore_roundtrip(client):
    backup = client.get("/api/settings/backup", headers=HDR)
    assert backup.status_code == 200
    data = backup.json()
    # backup contains the real secret (that is its purpose; PIN-protected)
    assert data["sites"][0]["growatt"]["password"] == "gw-secret"

    # wipe the stored password, then restore from the backup
    settings_store.save_state(make_state([make_site("el-durazno")]))
    assert settings_store.get_site("el-durazno")["growatt"]["password"] == ""

    r = client.post("/api/settings/restore", json=data, headers=HDR)
    assert r.status_code == 200
    assert settings_store.get_site("el-durazno")["growatt"]["password"] == "gw-secret"
    assert settings_store.get_site("el-durazno")["alerts"]["soc_min"] == 30


def test_restore_rejects_invalid_backup(client):
    before = settings_store.backup_state()
    r = client.post("/api/settings/restore", json={"sites": "kaputt"}, headers=HDR)
    assert r.status_code == 400
    r = client.post("/api/settings/restore",
                    json={"sites": [make_site("x", latitude=999)]}, headers=HDR)
    assert r.status_code == 400
    assert settings_store.backup_state() == before  # untouched on failure


# ── collector status ─────────────────────────────────────────────────────────
def test_collector_status_in_settings_status(client, monkeypatch):
    monkeypatch.setattr(db, "health", lambda: False)  # skip Influx queries
    collector_status.record_success("el-durazno", "webhook")
    collector_status.record_error("el-durazno", "growatt", RuntimeError("login failed"))
    r = client.get("/api/settings/status", headers=HDR)
    assert r.status_code == 200
    site = r.json()["sites"][0]
    assert site["collectors"]["webhook"]["last_success"]
    assert site["collectors"]["growatt"]["error"] == "login failed"


def test_alerts_settings_validation(client):
    site = make_site("el-durazno", alerts={"soc_min": 150})
    r = client.post("/api/settings", json={"sites": [site]}, headers=HDR)
    assert r.status_code == 400
    site = make_site("el-durazno", alerts={"fire_min_category": "banana"})
    r = client.post("/api/settings", json={"sites": [site]}, headers=HDR)
    assert r.status_code == 400
    # empty string disables a check and is stored as None
    site = make_site("el-durazno", alerts={"soc_min": "", "enabled": True})
    r = client.post("/api/settings", json={"sites": [site]}, headers=HDR)
    assert r.status_code == 200
    assert settings_store.get_site("el-durazno")["alerts"]["soc_min"] is None
