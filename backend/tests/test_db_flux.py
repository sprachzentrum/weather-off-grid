"""Flux helpers: timezone validation (also guards against Flux injection)."""
from datetime import timedelta, timezone as dt_timezone

import db


def test_flux_location_valid_name():
    prelude = db.flux_location("America/Argentina/Cordoba")
    assert 'timezone.location(name: "America/Argentina/Cordoba")' in prelude
    assert prelude.startswith('import "timezone"')


def test_flux_location_rejects_injection():
    evil = 'X") |> yield()\nimport "evil'
    prelude = db.flux_location(evil)
    assert "evil" not in prelude
    assert 'timezone.location(name: "UTC")' in prelude


def test_flux_location_empty_falls_back_to_utc():
    assert 'name: "UTC"' in db.flux_location(None)
    assert 'name: "UTC"' in db.flux_location("")


def test_tzinfo_for():
    from datetime import datetime

    tz = db.tzinfo_for("America/Argentina/Cordoba")
    offset = datetime(2026, 7, 16, tzinfo=dt_timezone.utc).astimezone(tz).utcoffset()
    assert offset == timedelta(hours=-3)
    # pattern-valid but nonexistent zone, and pattern-invalid name -> UTC
    probe = datetime(2026, 1, 1, tzinfo=dt_timezone.utc)
    assert probe.astimezone(db.tzinfo_for("Not/A/Zone")).utcoffset() == timedelta(0)
    assert probe.astimezone(db.tzinfo_for("Nope!!")).utcoffset() == timedelta(0)
