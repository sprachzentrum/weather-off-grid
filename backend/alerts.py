"""
Configurable alert thresholds + e-mail notifications.

Every site can define thresholds (battery SOC, wind, heat, cold/frost, fire
danger category) in its settings under the "alerts" group. Two consumers:

  * GET /api/alerts evaluates the thresholds against the latest stored data so
    the dashboard can show a warning banner - independent of e-mail.
  * A background checker (run_checker) re-evaluates every CHECK_INTERVAL and
    e-mails newly triggered alerts over the existing SMTP transport, with a
    per-(site, alert) cooldown so a hovering value does not spam the inbox.

The evaluation itself (`evaluate`) is a pure function over plain dicts so it is
trivially testable without InfluxDB.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
import time
from email.message import EmailMessage

import config
import db
import settings_store
from forecast import fire_danger

log = logging.getLogger("alerts")

CHECK_INTERVAL = 900          # seconds between background evaluations
COOLDOWN = 6 * 3600           # re-notify the same alert at most every 6 h
STALE_DATA_MAX_AGE_S = 3 * 3600  # ignore readings older than this

FIRE_ORDER = ["low", "moderate", "high", "very_high", "extreme"]

# Per-site defaults; every value can be overridden in site["alerts"].
# A threshold set to None disables that single check.
DEFAULTS: dict = {
    "enabled": False,              # e-mail notifications on/off
    "email_to": "",                # empty -> reports recipient
    "soc_min": 20.0,               # % - battery below this
    "wind_max": 80.0,              # km/h - wind/gust above this
    "heat_max": 38.0,              # °C - outdoor temperature above this
    "cold_min": 0.0,               # °C - outdoor temperature at/below this
    "fire_min_category": "very_high",  # alert at this FFDI category or worse
}

# Validation ranges for the numeric thresholds (used by settings_store).
NUMERIC_LIMITS: dict[str, tuple[float, float]] = {
    "soc_min": (0.0, 100.0),
    "wind_max": (0.0, 500.0),
    "heat_max": (-60.0, 70.0),
    "cold_min": (-60.0, 70.0),
}

LABELS_DE = {
    "soc": "Batterie niedrig",
    "wind": "Sturmwarnung",
    "heat": "Hitzewarnung",
    "cold": "Frost-/Kältewarnung",
    "fire": "Feuergefahr",
}


def thresholds_for(site: dict) -> dict:
    """Effective thresholds for a site: defaults overlaid with stored values."""
    merged = dict(DEFAULTS)
    stored = site.get("alerts")
    if isinstance(stored, dict):
        for key in DEFAULTS:
            if key in stored:
                merged[key] = stored[key]
    return merged


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def evaluate(thresholds: dict, weather: dict, energy: dict,
             fire_category: str | None = None) -> list[dict]:
    """Pure threshold evaluation -> list of active alerts.

    weather/energy are flat latest-value dicts (db.latest_fields shape).
    Missing readings or disabled (None) thresholds simply skip that check.
    """
    alerts: list[dict] = []

    def add(key: str, value, threshold, text: str) -> None:
        alerts.append({
            "key": key,
            "label": LABELS_DE.get(key, key),
            "value": value,
            "threshold": threshold,
            "text": text,
        })

    soc = _num(energy.get("battery_soc"))
    soc_min = _num(thresholds.get("soc_min"))
    if soc is not None and soc_min is not None and soc < soc_min:
        add("soc", soc, soc_min, f"Batterie {soc:.0f} % (Schwelle {soc_min:.0f} %)")

    wind = max((v for v in (_num(weather.get("wind_speed")),
                            _num(weather.get("wind_gust"))) if v is not None),
               default=None)
    wind_max = _num(thresholds.get("wind_max"))
    if wind is not None and wind_max is not None and wind >= wind_max:
        add("wind", wind, wind_max, f"Wind/Böen {wind:.0f} km/h (Schwelle {wind_max:.0f} km/h)")

    temp = _num(weather.get("temperature_outdoor"))
    heat_max = _num(thresholds.get("heat_max"))
    if temp is not None and heat_max is not None and temp >= heat_max:
        add("heat", temp, heat_max, f"Temperatur {temp:.1f} °C (Schwelle {heat_max:.0f} °C)")

    cold_min = _num(thresholds.get("cold_min"))
    if temp is not None and cold_min is not None and temp <= cold_min:
        add("cold", temp, cold_min, f"Temperatur {temp:.1f} °C (Schwelle {cold_min:.0f} °C)")

    min_cat = thresholds.get("fire_min_category")
    if (fire_category in FIRE_ORDER and min_cat in FIRE_ORDER
            and FIRE_ORDER.index(fire_category) >= FIRE_ORDER.index(min_cat)):
        add("fire", fire_category, min_cat, f"Feuergefahr-Kategorie „{fire_category}“")

    return alerts


def _fresh_latest(bucket: str, measurement: str, site_id: str) -> dict:
    """Latest fields, but empty when the newest reading is too old - stale
    sensor data must not keep an alert alive for days."""
    fields = db.latest_fields(bucket, measurement, site_id=site_id)
    ts = fields.get("_time")
    if hasattr(ts, "timestamp"):
        from datetime import datetime, timezone

        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age > STALE_DATA_MAX_AGE_S:
            return {}
    return fields


def current_alerts(site: dict) -> list[dict]:
    """Evaluate a site's thresholds against the latest stored readings."""
    sid = site["site_id"]
    weather = _fresh_latest(config.BUCKET_WEATHER, "station", sid)
    energy = _fresh_latest(config.BUCKET_ENERGY, "energy", sid)

    fire_category = None
    t = _num(weather.get("temperature_outdoor"))
    h = _num(weather.get("humidity_outdoor"))
    w = _num(weather.get("wind_speed"))
    if t is not None and h is not None and w is not None:
        df = fire_danger.drought_factor(fire_danger.days_since_rain(sid))
        ffdi = fire_danger.compute_ffdi(t, h, w, df)
        fire_category = fire_danger.categorise(ffdi)["category"]

    return evaluate(thresholds_for(site), weather, energy, fire_category)


# ── e-mail notification ──────────────────────────────────────────────────────
def send_alert_email(site: dict, alerts: list[dict], recipient: str) -> None:
    """E-mail active alerts over the reports SMTP transport. Raises on errors."""
    if not config.smtp_enabled():
        raise RuntimeError("SMTP not configured (SMTP_HOST / REPORT_EMAIL_FROM)")
    if not recipient:
        raise RuntimeError("no alert recipient configured")

    name = site.get("name", site["site_id"])
    lines = [f"⚠ {a['label']}: {a['text']}" for a in alerts]
    msg = EmailMessage()
    msg["Subject"] = f"Warnung {name}: " + ", ".join(a["label"] for a in alerts)
    msg["From"] = config.REPORT_EMAIL_FROM
    msg["To"] = recipient
    msg.set_content(
        f"Aktive Warnungen für {name}:\n\n" + "\n".join(lines) +
        "\n\nWeather Off-Grid – automatische Benachrichtigung."
    )
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
        if config.SMTP_STARTTLS:
            smtp.starttls(context=ssl.create_default_context())
        if config.SMTP_USER:
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.send_message(msg)
    log.info("alert e-mail sent to %s for site %s (%s)",
             recipient, site["site_id"], ", ".join(a["key"] for a in alerts))


# (site_id, alert key) -> monotonic time of the last notification.
_last_sent: dict[tuple[str, str], float] = {}


def _due(site_id: str, alerts: list[dict]) -> list[dict]:
    """Alerts that have not been notified within the cooldown window."""
    now = time.monotonic()
    due = [a for a in alerts
           if now - _last_sent.get((site_id, a["key"]), float("-inf")) >= COOLDOWN]
    return due


def _mark_sent(site_id: str, alerts: list[dict]) -> None:
    now = time.monotonic()
    for a in alerts:
        _last_sent[(site_id, a["key"])] = now


async def run_checker() -> None:
    """Background task: evaluate all sites and e-mail newly triggered alerts."""
    log.info("alert checker started (every %ds)", CHECK_INTERVAL)
    while True:
        try:
            for site in settings_store.sites():
                th = thresholds_for(site)
                if not th.get("enabled"):
                    continue
                active = await asyncio.to_thread(current_alerts, site)
                due = _due(site["site_id"], active)
                if not due:
                    continue
                recipient = (th.get("email_to")
                             or settings_store.reports_config().get("email_to")
                             or config.REPORT_EMAIL_TO)
                if not (config.smtp_enabled() and recipient):
                    log.warning("[%s] %d alert(s) active but no SMTP/recipient configured",
                                site["site_id"], len(due))
                    _mark_sent(site["site_id"], due)  # avoid warn-spam every cycle
                    continue
                try:
                    await asyncio.to_thread(send_alert_email, site, due, recipient)
                    _mark_sent(site["site_id"], due)
                except Exception as exc:  # noqa: BLE001
                    log.warning("[%s] alert e-mail failed: %s", site["site_id"], exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("alert checker error: %s", exc)
        await asyncio.sleep(CHECK_INTERVAL)


def reset_for_test() -> None:
    _last_sent.clear()
