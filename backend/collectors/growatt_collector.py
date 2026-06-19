"""
Growatt collector - polls the ShinePhone / legacy API and stores inverter +
battery telemetry in the `energy` bucket.

The SPF 5000 ES is an off-grid storage inverter that does NOT support Growatt's
modern V1 OpenAPI, so we use the classic username/password login provided by the
`growattServer` library. Different firmware registers the SPF as either a
"storage" or a "mix" device, and field names vary between models, so values are
extracted defensively by searching the response for a list of candidate keys.

Fully graceful: if no credentials are configured the collector logs once and
returns without raising, so the backend runs fine for weather-only users.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import config
import db

log = logging.getLogger("growatt")

# Candidate key names per metric. Growatt is wildly inconsistent across models,
# so we accept the first one that yields a number.
FIELD_CANDIDATES: dict[str, list[str]] = {
    "battery_soc": ["capacity", "SOC", "soc", "batterySoc", "capacity1"],
    "battery_voltage": ["vBat", "batteryVoltage", "vbat", "batVolt"],
    "battery_power": ["batPower", "batteryPower", "pBat", "chargePower"],
    "pv_power": ["ppv", "pPv", "ppvTotal", "solarPower", "ppv1"],
    "pv_energy_today": ["epvToday", "epvtoday", "ppvToday", "eToday", "epv1Today"],
    "load_power": ["loadPower", "activePower", "outPutPower", "pacToUser", "rLoadPower"],
    "load_energy_today": ["elocalLoadToday", "loadEnergyToday", "eToUserToday"],
    "inverter_temperature": ["temperature", "invTemp", "ipmTemperature", "temp"],
}
STATUS_CANDIDATES = ["statusText", "status", "storageStatus", "deviceStatus"]


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, "", "null"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _dig(data: Any, keys: list[str]) -> float | None:
    """Recursively search a nested dict/list for the first candidate key with a numeric value."""
    if isinstance(data, dict):
        for key in keys:
            if key in data:
                num = _to_float(data[key])
                if num is not None:
                    return num
        for value in data.values():
            found = _dig(value, keys)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _dig(item, keys)
            if found is not None:
                return found
    return None


def _dig_str(data: Any, keys: list[str]) -> str | None:
    if isinstance(data, dict):
        for key in keys:
            if key in data and data[key] not in (None, ""):
                return str(data[key])
        for value in data.values():
            found = _dig_str(value, keys)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _dig_str(item, keys)
            if found is not None:
                return found
    return None


def extract_fields(raw: dict) -> dict:
    """Map a raw Growatt response (any shape) to our metric field set."""
    fields: dict[str, Any] = {}
    for name, candidates in FIELD_CANDIDATES.items():
        value = _dig(raw, candidates)
        if value is not None:
            fields[name] = value

    status = _dig_str(raw, STATUS_CANDIDATES)
    if status is not None:
        fields["inverter_status"] = status

    # Sign convention: battery_power positive = charging, negative = discharging.
    # Some models report a separate discharge field; normalise if present.
    discharge = _dig(raw, ["dischargePower", "pDischarge", "batteryDischarge"])
    if discharge and discharge > 0 and not fields.get("battery_power"):
        fields["battery_power"] = -discharge
    return fields


class GrowattSession:
    """Holds a logged-in growattServer client and the resolved plant/inverter ids."""

    def __init__(self, creds: dict) -> None:
        self.api = None
        self.creds = creds
        self.plant_id: str | None = creds.get("plant_id") or None
        self.inverter_sn: str | None = creds.get("inverter_sn") or None

    def login(self) -> None:
        import growattServer  # imported lazily so the lib is optional

        self.api = growattServer.GrowattApi(add_random_user_id=True)
        result = self.api.login(self.creds.get("username"), self.creds.get("password"))
        if not result or not result.get("success", True):
            raise RuntimeError(f"growatt login failed: {result}")

        # Resolve plant + inverter if the user did not pin them in .env.
        if not self.plant_id:
            plants = self.api.plant_list(result.get("user", {}).get("id", ""))
            data = plants.get("data") if isinstance(plants, dict) else plants
            if data:
                self.plant_id = str(data[0].get("plantId") or data[0].get("id"))
        if not self.inverter_sn and self.plant_id:
            devices = self.api.device_list(self.plant_id)
            data = devices.get("data") if isinstance(devices, dict) else devices
            if data:
                self.inverter_sn = str(
                    data[0].get("deviceSn") or data[0].get("sn") or data[0].get("deviceSN")
                )
        log.info("growatt session ready (plant=%s, inverter=%s)", self.plant_id, self.inverter_sn)

    def fetch(self) -> dict:
        """Try storage then mix detail endpoints; return the first non-empty payload."""
        sn = self.inverter_sn
        merged: dict = {}
        for method_name in ("storage_detail", "storage_params", "mix_info", "mix_detail"):
            method = getattr(self.api, method_name, None)
            if method is None or not sn:
                continue
            try:
                if method_name in ("mix_detail",) and self.plant_id:
                    raw = method(self.plant_id, sn)
                else:
                    raw = method(sn)
                if isinstance(raw, dict):
                    merged.update(raw)
            except Exception as exc:  # noqa: BLE001
                log.debug("growatt %s failed: %s", method_name, exc)
        return merged


def _growatt_ready(site: dict) -> bool:
    gw = site.get("growatt") or {}
    return bool(gw.get("enabled") and gw.get("username") and gw.get("password"))


def _poll_blocking(session: GrowattSession | None, creds: dict) -> tuple[GrowattSession, dict]:
    """Synchronous poll run in a worker thread; (re)logs in as needed."""
    if session is None or session.api is None:
        session = GrowattSession(creds)
        session.login()
    try:
        raw = session.fetch()
    except Exception:  # noqa: BLE001 - likely an expired session, retry once
        session = GrowattSession(creds)
        session.login()
        raw = session.fetch()
    return session, raw


def _test_blocking(creds: dict) -> dict:
    try:
        session = GrowattSession(creds)
        session.login()
        raw = session.fetch()
        fields = extract_fields(raw)
        if fields:
            return {"ok": True, "detail": f"OK - SOC {fields.get('battery_soc', '?')}%, {len(fields)} Felder"}
        return {"ok": True, "detail": "Login OK, aber keine Inverter-Felder erkannt"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)}


async def test_connection(site: dict) -> dict:
    """Live login + fetch test used by POST /api/settings/test."""
    if not _growatt_ready(site):
        return {"ok": False, "detail": "Growatt nicht konfiguriert (Login fehlt)"}
    return await asyncio.to_thread(_test_blocking, site.get("growatt") or {})


async def run_poller(site: dict) -> None:
    sid = site["site_id"]
    if not _growatt_ready(site):
        log.info("[%s] growatt collector disabled - skipping", sid)
        return
    creds = site.get("growatt") or {}
    log.info("[%s] growatt collector started (every %ds)", sid, config.GROWATT_POLL_INTERVAL)

    session: GrowattSession | None = None
    while True:
        try:
            session, raw = await asyncio.to_thread(_poll_blocking, session, creds)
            fields = extract_fields(raw)
            if fields:
                db.write_point(
                    config.BUCKET_ENERGY,
                    "energy",
                    fields,
                    tags={"inverter": session.inverter_sn or "unknown", "site_id": sid},
                )
                log.info("[%s] growatt stored %d fields (soc=%s)", sid, len(fields), fields.get("battery_soc"))
            else:
                log.warning("[%s] growatt poll returned no recognisable fields", sid)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s] growatt poll failed: %s", sid, exc)
            session = None  # force re-login next round
        await asyncio.sleep(config.GROWATT_POLL_INTERVAL)
