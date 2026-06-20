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

# Candidate key names per metric. Growatt is wildly inconsistent across models.
# Candidates are tried in order across the whole (nested) response.
#
# battery_power is handled separately (sign convention) in extract_fields.
FIELD_CANDIDATES: dict[str, list[str]] = {
    "battery_soc": ["capacity", "SOC", "soc", "batterySoc", "capacity1"],
    "battery_voltage": ["vBat", "vbat", "batteryVoltage", "batVolt", "batVoltage",
                        "vBatteryVoltage", "vBatt"],
    # PV power: "ppv" is the TOTAL and comes first (e.g. storageDetailBean.ppv=727);
    # per-string ppv1/ppv2 are fallbacks (ppv2 is often 0 on a single-MPPT SPF).
    # prefer_nonzero + the multi-occurrence search skip a stray top-level ppv=0,
    # and extract_fields computes vpv*ipv as a last resort.
    "pv_power": ["ppv", "ppv1", "pPv", "pvPower", "ppvTotal", "ppvtotal",
                 "solarPower", "ppv2"],
    # DAILY PV yield only. Must NOT include lifetime totals (epvTotal/epv1Tot/
    # eTotal): those are a different quantity (~9000 kWh) and, when epvToday is
    # absent/zero, prefer_nonzero would otherwise fall through and report the
    # lifetime total as "today". ShinePhone shows e.g. "Heute 0.4 / Gesamt 9058.7".
    "pv_energy_today": ["epvToday", "epvtoday", "epv1Today", "epv2Today",
                        "ppvToday", "eToday"],
    # Real local load for SPF off-grid models. Deliberately EXCLUDES "activePower"
    # and any rated/nominal field ("ratedPower"/"maxPower"/"rateVA") which report
    # the inverter's 5000 W nameplate, not the actual consumption.
    "load_power": ["pLocalLoad", "loadPower", "localLoadPower", "loadPowerTotal",
                   "outPutPower", "pacToUser", "rLoadPower"],
    "load_energy_today": ["elocalLoadToday", "loadEnergyToday", "eToUserToday",
                          "eToUser", "eLoadToday", "useEnergyToday", "eopDischrToday"],
    "inverter_temperature": ["tempInverter", "temperature", "invTemp",
                             "ipmTemperature", "temp", "temp1", "temp2"],
}
STATUS_CANDIDATES = ["statusText", "status", "storageStatus", "deviceStatus"]

# Fields where a 0 reading usually means "wrong field"; prefer the first
# non-zero candidate (these candidate lists hold only same-quantity fields, so
# this cannot accidentally pick a rated/nominal value).
PREFER_NONZERO = {"battery_voltage", "pv_power", "inverter_temperature",
                  "load_energy_today", "pv_energy_today"}


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, "", "null"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _find_key_value(data: Any, key: str, prefer_nonzero: bool = False) -> tuple[float | None, str | None]:
    """
    Find a single `key` anywhere in the tree, with its dotted path.

    With prefer_nonzero, a non-zero occurrence beats a zero one - some SPF
    responses repeat a key (e.g. a top-level ppv=0 plus the real
    storageDetailBean.ppv=727), and we want the meaningful value.
    """
    found_zero: list = [None, None]  # (value, path)

    def walk(d: Any, p: str):
        if isinstance(d, dict):
            if key in d:
                num = _to_float(d[key])
                if num is not None:
                    if num != 0 or not prefer_nonzero:
                        return num, p + key
                    if found_zero[0] is None:
                        found_zero[0], found_zero[1] = num, p + key
            for k, v in d.items():
                r = walk(v, f"{p}{k}.")
                if r:
                    return r
        elif isinstance(d, list):
            for i, it in enumerate(d):
                r = walk(it, f"{p}{i}.")
                if r:
                    return r
        return None

    r = walk(data, "")
    return r if r else (found_zero[0], found_zero[1])


def _resolve(data: Any, keys: list[str], prefer_nonzero: bool = False) -> tuple[float | None, str | None]:
    """
    Resolve a metric by trying each candidate (in priority order) across the
    whole response. With prefer_nonzero, the first non-zero match wins and a
    zero match is only used as a last resort.
    """
    zero: tuple[float | None, str | None] = (None, None)
    for key in keys:
        val, mk = _find_key_value(data, key, prefer_nonzero=prefer_nonzero)
        if val is None:
            continue
        if not prefer_nonzero or val != 0:
            return val, mk
        if zero[0] is None:
            zero = (val, mk)
    return zero


def _dig(data: Any, keys: list[str]) -> float | None:
    return _resolve(data, keys)[0]


def _flatten(data: Any, prefix: str = "") -> dict:
    """Flatten a nested dict/list into {dotted.path: value} for debug logging."""
    out: dict = {}
    if isinstance(data, dict):
        for k, v in data.items():
            out.update(_flatten(v, f"{prefix}{k}."))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            out.update(_flatten(v, f"{prefix}{i}."))
    else:
        out[prefix.rstrip(".")] = data
    return out


_debug_logged = False


def log_raw_fields(raw: dict) -> None:
    """
    Log the full Growatt response once per process: every field, plus which
    source field each metric resolved to. Use this to identify the correct
    load_power field on a given SPF model (the rated 5000 W vs. real load).
    """
    global _debug_logged
    if _debug_logged or not isinstance(raw, dict) or not raw:
        return
    _debug_logged = True
    flat = {k: v for k, v in _flatten(raw).items() if v not in (None, "")}

    # The actionable list: numeric fields with a value != 0 - the real PV power,
    # battery voltage, charge/discharge etc. live here.
    nonzero = {k: v for k, v in flat.items() if _to_float(v) not in (None, 0.0)}
    log.info("=== Growatt: %d non-zero numeric fields (identify PV/voltage/charge here) ===", len(nonzero))
    for k in sorted(nonzero):
        log.info("    %s = %s", k, nonzero[k])

    log.info("=== Growatt: all %d fields ===", len(flat))
    for k in sorted(flat):
        log.info("    %s = %s", k, flat[k])

    log.info("=== Resolved metric <- source field ===")
    for name, cands in FIELD_CANDIDATES.items():
        val, key = _resolve(raw, cands, prefer_nonzero=(name in PREFER_NONZERO))
        log.info("    %s <- %s = %s", name, key, val)
    bp = _battery_power(raw)
    log.info("    battery_power (signed, +=charging) = %s", bp)


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


def _battery_power(raw: dict) -> float | None:
    """
    Battery power with the dashboard sign convention: POSITIVE = charging,
    NEGATIVE = discharging.

    SPF models report a NEGATIVE batPower while charging, so the raw value is
    inverted. Explicit charge/discharge fields, when present, take precedence.
    """
    charge, _ = _resolve(raw, ["chargePower", "pCharge", "batteryChargePower", "chargePwr"])
    discharge, _ = _resolve(raw, ["dischargePower", "pDischarge", "batteryDischarge", "dischargePwr"])
    if (charge and charge != 0) or (discharge and discharge != 0):
        return round((charge or 0.0) - (discharge or 0.0), 1)
    raw_bat, _ = _resolve(raw, ["batPower", "pBat", "batteryPower"])
    if raw_bat is not None:
        return round(-raw_bat, 1)  # invert: SPF reports negative while charging
    return None


def extract_fields(raw: dict) -> dict:
    """Map a raw Growatt response (any shape) to our metric field set."""
    fields: dict[str, Any] = {}
    for name, candidates in FIELD_CANDIDATES.items():
        value, src = _resolve(raw, candidates, prefer_nonzero=(name in PREFER_NONZERO))
        if value is not None:
            fields[name] = value
        # Verify the daily-yield mapping every poll: a value in the thousands
        # means a lifetime total leaked into the daily field again.
        if name == "pv_energy_today":
            log.info("pv_energy_today <- %s = %s kWh", src, value)

    # Battery power with the correct sign (+ = charging, - = discharging).
    bp = _battery_power(raw)
    if bp is not None:
        fields["battery_power"] = bp

    # PV power fallback: ppv often reads 0 on SPF while the array is producing.
    # If no PV power field gave a value, derive it from per-string voltage*current.
    if not fields.get("pv_power"):
        pv = 0.0
        for vk, ik in (("vpv1", "ipv1"), ("vpv2", "ipv2")):
            v = _find_key_value(raw, vk)[0]
            i = _find_key_value(raw, ik)[0]
            if v and i:
                pv += v * i
        if pv > 0:
            fields["pv_power"] = round(pv, 1)

    status = _dig_str(raw, STATUS_CANDIDATES)
    if status is not None:
        fields["inverter_status"] = status
    return fields


# The SPF 5000 ES is an off-grid storage inverter served by the classic
# ShinePhone server, NOT the modern openapi.growatt.com endpoint (which returns
# 403 for these accounts). We try the configured server first, then known
# ShinePhone hosts, and identify as the ShinePhone app.
GROWATT_AGENT = "ShinePhone/8.1.8.6"
DEFAULT_GROWATT_SERVERS = [
    "https://server.growatt.com",
    "https://server-api.growatt.com",
]


def _make_api(server_url: str):
    """Build a GrowattApi pinned to a specific server with the ShinePhone agent.

    Handles growattServer version differences: agent_identifier may or may not be
    a constructor argument, and server_url is set via the attribute used for all
    request URLs (with the trailing slash the library expects).
    """
    import growattServer

    try:
        api = growattServer.GrowattApi(add_random_user_id=True, agent_identifier=GROWATT_AGENT)
    except TypeError:
        # Older lib without agent_identifier kwarg - set the UA on the session.
        api = growattServer.GrowattApi(add_random_user_id=True)
        try:
            api.session.headers.update({"User-Agent": GROWATT_AGENT})
        except Exception:  # noqa: BLE001
            pass
    api.server_url = server_url.rstrip("/") + "/"
    return api


def _server_candidates(creds: dict) -> list[str]:
    """Configured server first (if any), then the known ShinePhone fallbacks."""
    out: list[str] = []
    for url in [creds.get("server_url"), *DEFAULT_GROWATT_SERVERS]:
        if url and url.rstrip("/") not in [u.rstrip("/") for u in out]:
            out.append(url)
    return out


class GrowattSession:
    """Holds a logged-in growattServer client and the resolved plant/inverter ids."""

    def __init__(self, creds: dict) -> None:
        self.api = None
        self.creds = creds
        self.server_url: str | None = None
        self.plant_id: str | None = creds.get("plant_id") or None
        self.inverter_sn: str | None = creds.get("inverter_sn") or None

    def login(self) -> None:
        # Try each candidate server in turn; a 403/HTTP error means "wrong server
        # for this account", so fall through to the next one.
        last_exc: Exception | None = None
        result = None
        for url in _server_candidates(self.creds):
            try:
                api = _make_api(url)
                result = api.login(self.creds.get("username"), self.creds.get("password"))
                if not result or not result.get("success", True):
                    raise RuntimeError(f"login rejected: {result}")
                self.api = api
                self.server_url = url
                log.info("growatt login OK via %s", url)
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                self.api = None
                log.warning("growatt login via %s failed: %s", url, exc)
        if self.api is None:
            raise RuntimeError(f"growatt login failed on all servers: {last_exc}")

        # Resolve plant + inverter if the user did not pin them in settings.
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
        log_raw_fields(raw)
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
            log_raw_fields(raw)
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
