"""Ecowitt collector: unit conversions, derived metrics, payload parsing."""
import pytest

from collectors import ecowitt_collector as eco


# ── unit conversions ────────────────────────────────────────────────────────
def test_conversions():
    assert eco.f_to_c(32.0) == 0.0
    assert eco.f_to_c(212.0) == 100.0
    assert round(eco.mph_to_kmh(10.0), 3) == 16.093
    assert round(eco.inhg_to_hpa(29.92), 1) == 1013.2
    assert eco.inch_to_mm(1.0) == 25.4


def test_dewpoint():
    # Saturated air: dew point == temperature.
    assert round(eco.dewpoint_c(20.0, 100.0), 1) == 20.0
    # Drier air: dew point below temperature.
    assert eco.dewpoint_c(20.0, 50.0) < 20.0
    assert eco.dewpoint_c(20.0, 0.0) is None


def test_apparent_temp():
    # Wind lowers the apparent temperature.
    calm = eco.apparent_temp_c(20.0, 50.0, 0.0)
    windy = eco.apparent_temp_c(20.0, 50.0, 40.0)
    assert windy < calm


# ── webhook payload parsing ─────────────────────────────────────────────────
def test_parse_webhook_form_basic():
    form = {
        "tempf": "68.0",          # 20 °C
        "humidity": "50",
        "windspeedmph": "10",     # 16.09 km/h
        "baromrelin": "29.92",    # 1013.2 hPa
        "dailyrainin": "0.5",     # 12.7 mm
    }
    fields = eco.parse_webhook_form(form)
    assert fields["temperature_outdoor"] == 20.0
    assert fields["humidity_outdoor"] == 50.0
    assert fields["wind_speed"] == 16.09
    assert fields["pressure_relative"] == pytest.approx(1013.2, abs=0.1)
    assert fields["rain_daily"] == 12.7
    # derived
    assert "dewpoint" in fields
    assert "temperature_feels_like" in fields


def test_parse_webhook_form_skips_garbage():
    fields = eco.parse_webhook_form({"tempf": "not-a-number", "humidity": ""})
    assert fields == {}


def test_parse_webhook_form_drops_implausible_values():
    """Out-of-range values (corrupt or malicious) must not reach the database."""
    form = {
        "tempf": "500",        # 260 °C -> dropped
        "humidity": "150",     # >100 % -> dropped
        "windspeedmph": "10",
    }
    fields = eco.parse_webhook_form(form)
    assert "temperature_outdoor" not in fields
    assert "humidity_outdoor" not in fields
    assert fields["wind_speed"] == 16.09


def test_parse_webhook_form_keeps_boundary_values():
    form = {"humidity": "100", "tempf": "-76"}  # -60 °C, 100 %
    fields = eco.parse_webhook_form(form)
    assert fields["humidity_outdoor"] == 100.0
    assert fields["temperature_outdoor"] == -60.0
