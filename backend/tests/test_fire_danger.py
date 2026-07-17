"""Fire danger: FFDI maths, categories, dry-spell logic (pure, no InfluxDB)."""
from datetime import date, timedelta

from forecast import fire_danger


def test_drought_factor_clamps():
    assert fire_danger.drought_factor(0) == 0.0
    assert fire_danger.drought_factor(4) == 2.0
    assert fire_danger.drought_factor(20) == 10.0
    assert fire_danger.drought_factor(1000) == 10.0


def test_compute_ffdi_monotonic():
    base = fire_danger.compute_ffdi(30.0, 30.0, 20.0, 5.0)
    hotter = fire_danger.compute_ffdi(40.0, 30.0, 20.0, 5.0)
    wetter = fire_danger.compute_ffdi(30.0, 80.0, 20.0, 5.0)
    windier = fire_danger.compute_ffdi(30.0, 30.0, 60.0, 5.0)
    assert hotter > base
    assert wetter < base
    assert windier > base
    assert fire_danger.compute_ffdi(0.0, 100.0, 0.0, 0.0) >= 0.0


def test_categorise_bands():
    assert fire_danger.categorise(0.0)["category"] == "low"
    assert fire_danger.categorise(5.0)["category"] == "moderate"
    assert fire_danger.categorise(12.0)["category"] == "high"
    assert fire_danger.categorise(24.0)["category"] == "very_high"
    assert fire_danger.categorise(50.0)["category"] == "extreme"
    assert fire_danger.categorise(999.0)["category"] == "extreme"
    assert fire_danger.categorise(7.0, "en")["label"] == "Moderate"


def test_days_since_rain(monkeypatch):
    today = date(2026, 7, 16)
    rain = {
        (today - timedelta(days=3)).isoformat(): 5.0,   # significant
        (today - timedelta(days=1)).isoformat(): 1.0,   # below threshold
    }
    monkeypatch.setattr(fire_danger, "daily_rain", lambda site_id, days=90: rain)
    assert fire_danger.days_since_rain("x", today.isoformat()) == 3


def test_days_since_rain_no_rain(monkeypatch):
    monkeypatch.setattr(fire_danger, "daily_rain", lambda site_id, days=90: {})
    assert fire_danger.days_since_rain("x", "2026-07-16") == fire_danger.RAIN_LOOKBACK_DAYS


def test_longest_dry_spell():
    assert fire_danger.longest_dry_spell({}) == 0
    rain = {
        "2026-07-01": 5.0,
        "2026-07-02": 0.0,
        # 03..05 missing -> treated as dry
        "2026-07-06": 0.5,
        "2026-07-07": 10.0,
        "2026-07-08": 0.0,
    }
    assert fire_danger.longest_dry_spell(rain) == 5  # 02..06
