"""
Central configuration, loaded from environment variables (.env in Docker).

Every credential and tunable lives here so the rest of the code never reads
os.environ directly. Nothing is hardcoded - an empty value simply disables the
related feature (e.g. no Growatt credentials -> Growatt collector is skipped).
"""
from __future__ import annotations

import os

try:
    # Allows running the CLI tools / app outside Docker with a local .env file.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is optional at runtime
    pass


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _get_float(name: str, default: float) -> float:
    raw = _get(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _get_int(name: str, default: int) -> int:
    raw = _get(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


# ── InfluxDB ──────────────────────────────────────────────────────────────
INFLUXDB_URL = _get("INFLUXDB_URL", "http://influxdb:8086")
INFLUXDB_TOKEN = _get("INFLUXDB_TOKEN")
INFLUXDB_ORG = _get("INFLUXDB_ORG", "offgrid")
BUCKET_WEATHER = _get("INFLUXDB_BUCKET_WEATHER", "weather")
BUCKET_ENERGY = _get("INFLUXDB_BUCKET_ENERGY", "energy")
BUCKET_FORECASTS = _get("INFLUXDB_BUCKET_FORECASTS", "forecasts")

# ── Ecowitt ───────────────────────────────────────────────────────────────
ECOWITT_APP_KEY = _get("ECOWITT_APP_KEY")
ECOWITT_API_KEY = _get("ECOWITT_API_KEY")
ECOWITT_MAC = _get("ECOWITT_MAC")

# ── Growatt (ShinePhone / legacy login) ───────────────────────────────────
GROWATT_USERNAME = _get("GROWATT_USERNAME")
GROWATT_PASSWORD = _get("GROWATT_PASSWORD")
GROWATT_PLANT_ID = _get("GROWATT_PLANT_ID")
GROWATT_INVERTER_SN = _get("GROWATT_INVERTER_SN")
# Classic ShinePhone server for off-grid storage inverters (SPF series).
GROWATT_SERVER_URL = _get("GROWATT_SERVER_URL", "https://server.growatt.com")

# ── Energy system (defaults; frontend may override per-request) ───────────
PV_KWP = _get_float("PV_KWP", 3.6)
PV_EFFICIENCY = _get_float("PV_EFFICIENCY", 0.75)
BATTERY_CAPACITY_KWH = _get_float("BATTERY_CAPACITY_KWH", 9.6)

# ── Location ──────────────────────────────────────────────────────────────
LATITUDE = _get_float("LATITUDE", -32.1559)
LONGITUDE = _get_float("LONGITUDE", -64.7916)
ALTITUDE = _get_float("ALTITUDE", 1000.0)
TIMEZONE = _get("TIMEZONE", "America/Argentina/Cordoba")

# ── Server ────────────────────────────────────────────────────────────────
BACKEND_PORT = _get_int("BACKEND_PORT", 8000)

# ── Admin / settings ──────────────────────────────────────────────────────
# Empty ADMIN_PIN = settings page is open (fine for trusted local networks).
ADMIN_PIN = _get("ADMIN_PIN")
# Where the persisted settings.json lives (mounted as a docker volume).
DATA_DIR = _get("DATA_DIR", "data")

# ── Polling intervals (seconds) ───────────────────────────────────────────
ECOWITT_POLL_INTERVAL = _get_int("ECOWITT_POLL_INTERVAL", 300)
GROWATT_POLL_INTERVAL = _get_int("GROWATT_POLL_INTERVAL", 300)
OPENMETEO_POLL_INTERVAL = _get_int("OPENMETEO_POLL_INTERVAL", 21600)  # 6h
NIGHT_SUMMARY_INTERVAL = _get_int("NIGHT_SUMMARY_INTERVAL", 21600)  # 6h

# ── Reports (PDF via e-mail) ──────────────────────────────────────────────
# SMTP transport credentials are secrets and live only here / in .env. The
# recipient, on/off and weekly|monthly schedule are user-editable in
# settings.json (these values seed the defaults on first start).
SMTP_HOST = _get("SMTP_HOST")
SMTP_PORT = _get_int("SMTP_PORT", 587)
SMTP_USER = _get("SMTP_USER")
SMTP_PASSWORD = _get("SMTP_PASSWORD")
SMTP_STARTTLS = _get("SMTP_STARTTLS", "true").lower() not in ("0", "false", "no")
REPORT_EMAIL_FROM = _get("REPORT_EMAIL_FROM")
REPORT_EMAIL_TO = _get("REPORT_EMAIL_TO")
REPORT_SCHEDULE = _get("REPORT_SCHEDULE", "weekly")  # weekly | monthly | off


def ecowitt_enabled() -> bool:
    """API poller needs all three keys; the webhook works without them."""
    return bool(ECOWITT_APP_KEY and ECOWITT_API_KEY and ECOWITT_MAC)


def growatt_enabled() -> bool:
    return bool(GROWATT_USERNAME and GROWATT_PASSWORD)


def smtp_enabled() -> bool:
    """Reports can be e-mailed only when an SMTP host and a From address exist."""
    return bool(SMTP_HOST and REPORT_EMAIL_FROM)
