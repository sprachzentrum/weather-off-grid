"""
Settings + multi-site store.

Persists the full configuration as JSON in DATA_DIR/settings.json (a docker
volume). On first start, when no settings.json exists, the store bootstraps an
in-memory default site from the .env values so the existing single-site behaviour
keeps working untouched - nothing is written until the user saves via the API.

A "site" is a self-contained profile: location + optional Ecowitt/Growatt
hardware + energy-system parameters. Every InfluxDB measurement is tagged with
the site_id, and collectors run once per site.

Secrets (currently only each site's Growatt password) are masked as "********"
when read back; an empty value on write means "keep the existing secret".
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import threading

import config

log = logging.getLogger("settings")

SETTINGS_PATH = os.path.join(config.DATA_DIR, "settings.json")
MASK = "********"
SCHEMA_VERSION = 1

_lock = threading.RLock()
_state: dict | None = None
_from_file = False  # True once settings.json has been loaded/saved


# ── slug / helpers ─────────────────────────────────────────────────────────
def slugify(name: str) -> str:
    s = (name or "").strip().lower()
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "site"


def _default_site_from_env() -> dict:
    """Build the initial site profile from the .env / config defaults."""
    return {
        "site_id": "el-durazno",
        "name": "El Durazno",
        "latitude": config.LATITUDE,
        "longitude": config.LONGITUDE,
        "altitude": config.ALTITUDE,
        "timezone": config.TIMEZONE,
        "ecowitt": {
            "enabled": config.ecowitt_enabled(),
            "app_key": config.ECOWITT_APP_KEY,
            "api_key": config.ECOWITT_API_KEY,
            "mac": config.ECOWITT_MAC,
        },
        "growatt": {
            "enabled": config.growatt_enabled(),
            "username": config.GROWATT_USERNAME,
            "password": config.GROWATT_PASSWORD,
            "plant_id": config.GROWATT_PLANT_ID,
            "inverter_sn": config.GROWATT_INVERTER_SN,
            "server_url": config.GROWATT_SERVER_URL,
        },
        "pv_kwp": config.PV_KWP,
        "pv_efficiency": config.PV_EFFICIENCY,
        "battery_capacity_kwh": config.BATTERY_CAPACITY_KWH,
        "wind_threshold_kmh": 10,
    }


def _default_state() -> dict:
    return {
        "version": SCHEMA_VERSION,
        "default_site": "el-durazno",
        "display": {
            "language": "de",
            "units": "metric",
            "refresh_interval": 300,
        },
        "sites": [_default_site_from_env()],
    }


def _normalise_site(site: dict, existing: dict | None = None) -> dict:
    """Coerce a (partial) site dict into the canonical shape, filling defaults."""
    base = existing or {
        "ecowitt": {"enabled": False, "app_key": "", "api_key": "", "mac": ""},
        "growatt": {"enabled": False, "username": "", "password": "",
                    "plant_id": "", "inverter_sn": "",
                    "server_url": "https://server.growatt.com"},
        "pv_kwp": 3.6, "pv_efficiency": 0.75, "battery_capacity_kwh": 9.6,
        "wind_threshold_kmh": 10, "altitude": 0, "timezone": "UTC",
        "latitude": 0.0, "longitude": 0.0,
    }
    out = copy.deepcopy(base)
    for key in ("name", "latitude", "longitude", "altitude", "timezone",
                "pv_kwp", "pv_efficiency", "battery_capacity_kwh", "wind_threshold_kmh"):
        if key in site and site[key] is not None:
            out[key] = site[key]

    for group in ("ecowitt", "growatt"):
        if group in site and isinstance(site[group], dict):
            out.setdefault(group, {})
            for k, v in site[group].items():
                out[group][k] = v

    # site_id: keep existing, else derive from name.
    out["site_id"] = (existing or {}).get("site_id") or site.get("site_id") or slugify(out.get("name", ""))
    out.setdefault("name", out["site_id"])
    return out


# ── load / save ────────────────────────────────────────────────────────────
def load() -> dict:
    global _state, _from_file
    with _lock:
        if _state is not None:
            return _state
        if os.path.exists(SETTINGS_PATH):
            try:
                with open(SETTINGS_PATH, encoding="utf-8") as fh:
                    _state = json.load(fh)
                _from_file = True
                log.info("loaded settings from %s (%d sites)", SETTINGS_PATH, len(_state.get("sites", [])))
            except Exception as exc:  # noqa: BLE001
                log.error("failed to read settings.json, using env defaults: %s", exc)
                _state = _default_state()
        else:
            _state = _default_state()
            log.info("no settings.json - bootstrapped default site from .env")
        return _state


def is_configured() -> bool:
    """True once the user has saved settings (settings.json exists)."""
    load()
    return _from_file


def _persist() -> None:
    global _from_file
    os.makedirs(config.DATA_DIR, exist_ok=True)
    tmp = SETTINGS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(_state, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, SETTINGS_PATH)
    _from_file = True
    log.info("settings saved (%d sites)", len(_state.get("sites", [])))


def save_state(new_state: dict) -> None:
    global _state
    with _lock:
        _state = new_state
        _persist()


# ── site access ────────────────────────────────────────────────────────────
def sites() -> list[dict]:
    return load().get("sites", [])


def site_ids() -> list[str]:
    return [s["site_id"] for s in sites()]


def default_site_id() -> str:
    st = load()
    sid = st.get("default_site")
    ids = site_ids()
    if sid in ids:
        return sid
    return ids[0] if ids else "default"


def get_site(site_id: str | None) -> dict | None:
    """Resolve a site by id; None/unknown id falls back to the default site."""
    items = sites()
    if not items:
        return None
    if site_id:
        for s in items:
            if s["site_id"] == site_id:
                return s
    target = default_site_id()
    for s in items:
        if s["site_id"] == target:
            return s
    return items[0]


def upsert_site(site: dict) -> dict:
    """Add or update a single site (merging into existing secrets). Persists."""
    with _lock:
        st = load()
        items = st.setdefault("sites", [])
        sid = site.get("site_id")
        existing = next((s for s in items if s["site_id"] == sid), None) if sid else None
        if existing is None and not sid:
            # No explicit id: match an existing site by name-slug, else make a
            # new unique id from the name.
            base = slugify(site.get("name", "site"))
            if base in site_ids():
                sid = base
                existing = next((s for s in items if s["site_id"] == sid), None)
            else:
                sid = base
                n = 2
                while sid in site_ids():
                    sid = f"{base}-{n}"; n += 1
            site = {**site, "site_id": sid}

        merged = _normalise_site(_unmask_site(site, existing), existing)
        if existing is not None:
            idx = items.index(existing)
            items[idx] = merged
        else:
            items.append(merged)
        if len(items) == 1:
            st["default_site"] = merged["site_id"]
        _persist()
        return merged


def delete_site(site_id: str) -> bool:
    with _lock:
        st = load()
        items = st.get("sites", [])
        new_items = [s for s in items if s["site_id"] != site_id]
        if len(new_items) == len(items):
            return False
        st["sites"] = new_items
        if st.get("default_site") == site_id and new_items:
            st["default_site"] = new_items[0]["site_id"]
        _persist()
        return True


# ── masking ────────────────────────────────────────────────────────────────
def _mask_site(site: dict) -> dict:
    out = copy.deepcopy(site)
    gw = out.get("growatt") or {}
    if gw.get("password"):
        gw["password"] = MASK
    return out


def _unmask_site(incoming: dict, existing: dict | None) -> dict:
    """Replace masked / empty secrets in incoming with the existing stored value."""
    out = copy.deepcopy(incoming)
    gw = out.get("growatt")
    if isinstance(gw, dict):
        pw = gw.get("password")
        if pw in (None, "", MASK):
            gw["password"] = ((existing or {}).get("growatt") or {}).get("password", "")
    return out


# ── public views ───────────────────────────────────────────────────────────
def public_settings() -> dict:
    """Full settings with secrets masked (for the admin GET /api/settings)."""
    st = load()
    return {
        "version": st.get("version", SCHEMA_VERSION),
        "configured": is_configured(),
        "default_site": default_site_id(),
        "display": st.get("display", {}),
        "sites": [_mask_site(s) for s in st.get("sites", [])],
    }


def apply_update(payload: dict) -> dict:
    """
    Bulk update from POST /api/settings. Accepts {display, default_site, sites}.
    Sites are merged by site_id, preserving masked secrets. Persists and returns
    the new masked view.
    """
    with _lock:
        st = load()
        if isinstance(payload.get("display"), dict):
            st.setdefault("display", {}).update(payload["display"])
        if isinstance(payload.get("sites"), list):
            existing_by_id = {s["site_id"]: s for s in st.get("sites", [])}
            new_sites = []
            for incoming in payload["sites"]:
                sid = incoming.get("site_id") or slugify(incoming.get("name", "site"))
                prev = existing_by_id.get(sid)
                new_sites.append(_normalise_site(_unmask_site({**incoming, "site_id": sid}, prev), prev))
            st["sites"] = new_sites
        if payload.get("default_site") and payload["default_site"] in [s["site_id"] for s in st.get("sites", [])]:
            st["default_site"] = payload["default_site"]
        _persist()
        return public_settings()


def frontend_config() -> dict:
    """
    Public config the frontend can fetch instead of config.js. No secrets.
    """
    st = load()
    return {
        "configured": is_configured(),
        "admin_pin_required": bool(config.ADMIN_PIN),
        "default_site": default_site_id(),
        "display": st.get("display", {}),
        "sites": [
            {
                "site_id": s["site_id"],
                "name": s.get("name", s["site_id"]),
                "latitude": s.get("latitude"),
                "longitude": s.get("longitude"),
                "has_battery": bool((s.get("growatt") or {}).get("enabled")),
                "pv_kwp": s.get("pv_kwp"),
                "pv_efficiency": s.get("pv_efficiency"),
                "battery_capacity_kwh": s.get("battery_capacity_kwh"),
                "wind_threshold_kmh": s.get("wind_threshold_kmh"),
            }
            for s in st.get("sites", [])
        ],
    }


# ── ecowitt webhook routing ────────────────────────────────────────────────
def _norm_mac(mac: str) -> str:
    return re.sub(r"[^0-9a-f]", "", (mac or "").lower())


def site_for_ecowitt(mac: str | None, passkey: str | None) -> dict | None:
    """
    Resolve which site a Custom Server webhook belongs to.

    Ecowitt sends either a raw MAC or a PASSKEY (uppercase MD5 of the MAC).
    Match on the raw MAC first, then on the PASSKEY hash. Falls back to the
    default site so single-station setups keep working without configuration.
    """
    items = sites()
    if not items:
        return None
    nmac = _norm_mac(mac) if mac else ""
    pk = (passkey or "").lower()
    for s in items:
        site_mac = _norm_mac((s.get("ecowitt") or {}).get("mac", ""))
        if not site_mac:
            continue
        if nmac and nmac == site_mac:
            return s
        if pk:
            digest = hashlib.md5(site_mac.upper().encode()).hexdigest()
            if pk == digest:
                return s
    return get_site(None)  # default site


def reset_for_test() -> None:
    """Test hook: drop in-memory state so the next load() re-reads/bootstraps."""
    global _state, _from_file
    with _lock:
        _state = None
        _from_file = False
