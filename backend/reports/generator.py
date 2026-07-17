"""
Weekly / monthly PDF report generator.

Aggregates the stored weather, energy and fire-danger history for one site from
InfluxDB, renders a set of matplotlib charts and lays everything out as a PDF
with reportlab. The PDF can be e-mailed over plain SMTP and is also cached on
disk so the frontend can download the latest one.

Used three ways:
  * the background scheduler (`run_scheduler`) e-mails every enabled site on the
    configured weekly/monthly cadence,
  * the API (`GET /api/reports/latest`) serves the cached PDF,
  * the CLI (`python -m reports.generator --site el-durazno --period weekly`).

matplotlib runs head-less (Agg backend, set before pyplot is imported) so it
works inside the slim container with no display.
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import smtplib
import ssl
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

import matplotlib
matplotlib.use("Agg")  # head-less: must be set before pyplot is imported
import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage,
)

import config
import db
import settings_store
from forecast import fire_danger, microclimate

log = logging.getLogger("reports")

PERIODS = {"weekly": 7, "monthly": 30}

# Brand palette (mirrors the dashboard's dark theme accents on a light page).
ACCENT = "#00d4aa"
INK = "#1a1a2e"
DIM = "#6b6b8a"
# Fire-danger category -> chart colour (matches the FFDI bands).
FIRE_COLORS = {
    "low": "#2faa5a", "moderate": "#e9c000", "high": "#e8833a",
    "very_high": "#e63946", "extreme": "#7a1020",
}


# ── timezone helpers ────────────────────────────────────────────────────────
def _tz(site: dict):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(site.get("timezone") or config.TIMEZONE or "UTC")
        except Exception:  # noqa: BLE001
            pass
    return timezone.utc


def _num(v):
    return float(v) if isinstance(v, (int, float)) else None


def _mean(xs: list) -> float | None:
    xs = [x for x in xs if isinstance(x, (int, float))]
    return sum(xs) / len(xs) if xs else None


# ── InfluxDB daily aggregation ──────────────────────────────────────────────
def _site_line(site_id: str | None) -> str:
    if not site_id:
        return ""
    try:
        is_default = site_id == settings_store.default_site_id()
    except Exception:  # noqa: BLE001
        is_default = False
    if is_default:
        return f' and (r.site_id == "{site_id}" or not exists r.site_id)'
    return f' and r.site_id == "{site_id}"'


def _daily(bucket: str, measurement: str, field: str, fn: str,
           site_id: str | None, days: int) -> dict[str, float]:
    """Per-day aggregate of a field -> {date_iso: value}. Empty on error.

    Days are the site's *local* calendar days (Flux `location` option), so the
    report's daily rows match the local span produced by _day_range()."""
    tz_name, tz = db.site_tz(site_id)
    flux = f'''{db.flux_location(tz_name)}
    from(bucket: "{bucket}")
      |> range(start: -{days}d)
      |> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "{field}"{_site_line(site_id)})
      |> aggregateWindow(every: 1d, fn: {fn}, timeSrc: "_start", createEmpty: false)
    '''
    out: dict[str, float] = {}
    try:
        for table in db.query(flux):
            for rec in table.records:
                v = rec.get_value()
                if v is not None:
                    out[rec.get_time().astimezone(tz).date().isoformat()] = float(v)
    except Exception as exc:  # noqa: BLE001
        log.debug("daily agg %s/%s failed: %s", field, fn, exc)
    return out


def _day_range(days: int, tz) -> list[str]:
    """The list of local calendar days the report covers (oldest first)."""
    today = datetime.now(tz).date()
    return [(today - timedelta(days=d)).isoformat() for d in range(days, 0, -1)]


# ── data gathering ──────────────────────────────────────────────────────────
def gather(site: dict, period: str) -> dict:
    """Collect every figure + daily series the report needs for one site."""
    sid = site["site_id"]
    days = PERIODS.get(period, 7)
    tz = _tz(site)
    span = _day_range(days, tz)
    W, E = config.BUCKET_WEATHER, config.BUCKET_ENERGY

    # Weather daily series.
    tmax = _daily(W, "station", "temperature_outdoor", "max", sid, days)
    tmin = _daily(W, "station", "temperature_outdoor", "min", sid, days)
    tmean = _daily(W, "station", "temperature_outdoor", "mean", sid, days)
    hum_min = _daily(W, "station", "humidity_outdoor", "min", sid, days)
    wind_mean = _daily(W, "station", "wind_speed", "mean", sid, days)
    wind_max = _daily(W, "station", "wind_speed", "max", sid, days)
    gust_max = _daily(W, "station", "wind_gust", "max", sid, days)
    press_mean = _daily(W, "station", "pressure_relative", "mean", sid, days)
    solar_mean = _daily(W, "station", "solar_radiation", "mean", sid, days)
    rain = fire_danger.daily_rain(sid, days + 1)

    # Energy daily series. Daily energy (kWh) ~= mean power (W) * 24 h / 1000.
    pv_mean = _daily(E, "energy", "pv_power", "mean", sid, days)
    load_mean = _daily(E, "energy", "load_power", "mean", sid, days)
    soc_mean = _daily(E, "energy", "battery_soc", "mean", sid, days)
    soc_min = _daily(E, "energy", "battery_soc", "min", sid, days)
    pv_kwh = {d: v * 24.0 / 1000.0 for d, v in pv_mean.items()}
    load_kwh = {d: v * 24.0 / 1000.0 for d, v in load_mean.items()}

    has_energy = bool(pv_mean or load_mean or soc_mean)

    # Fire-danger per day: project the dry-day counter across the span.
    fire_series: dict[str, dict] = {}
    dry = 0
    # seed the counter from history just before the window
    dry = fire_danger.days_since_rain(sid, span[0]) if span else 0
    for d in span:
        if rain.get(d, 0.0) > fire_danger.SIGNIFICANT_RAIN_MM:
            dry = 0
        df = fire_danger.drought_factor(dry)
        t = tmax.get(d)
        h = hum_min.get(d)
        v = wind_max.get(d)
        if t is not None and h is not None and v is not None:
            ffdi = fire_danger.compute_ffdi(t, h, v, df)
            fire_series[d] = {"ffdi": ffdi, **fire_danger.categorise(ffdi)}
        dry += 1

    # Weather summary.
    weather_summary = {
        "temp_avg": _mean(list(tmean.values())),
        "temp_min": min(tmin.values()) if tmin else None,
        "temp_max": max(tmax.values()) if tmax else None,
        "precip_total": sum(rain.get(d, 0.0) for d in span),
        "wind_avg": _mean(list(wind_mean.values())),
        "gust_max": max(gust_max.values()) if gust_max else None,
        "psh_total": sum(v * 24.0 / 1000.0 for v in solar_mean.values()) if solar_mean else None,
        "pressure_avg": _mean(list(press_mean.values())),
    }

    # Energy summary, incl. SOC near sunset (~18:00) / sunrise (~06:00) proxies.
    soc_sunset, soc_sunrise = _soc_at_hours(sid, days, tz)
    energy_summary = {
        "pv_total": sum(pv_kwh.values()) if pv_kwh else None,
        "load_total": sum(load_kwh.values()) if load_kwh else None,
        "consumption_avg": (sum(load_kwh.values()) / len(load_kwh)) if load_kwh else None,
        "night_consumption_avg": _night_consumption(sid, days, tz),
        "soc_sunset_avg": soc_sunset,
        "soc_sunrise_avg": soc_sunrise,
        "soc_min": min(soc_min.values()) if soc_min else None,
        "balance_avg": ((sum(pv_kwh.values()) - sum(load_kwh.values())) / max(1, len(span)))
        if (pv_kwh or load_kwh) else None,
    } if has_energy else None

    # Fire summary.
    cats_count: dict[str, int] = {}
    for info in fire_series.values():
        cats_count[info["category"]] = cats_count.get(info["category"], 0) + 1
    fire_summary = {
        "max_ffdi": max((i["ffdi"] for i in fire_series.values()), default=None),
        "categories": cats_count,
        "longest_dry_spell": fire_danger.longest_dry_spell(
            {d: rain.get(d, 0.0) for d in span}
        ),
    } if fire_series else None

    # Microclimate snapshot.
    micro = microclimate.get_statistics(sid)

    return {
        "site": site,
        "period": period,
        "days": days,
        "span": span,
        "generated_at": datetime.now(tz),
        "weather": weather_summary,
        "energy": energy_summary,
        "fire": fire_summary,
        "microclimate": micro,
        "series": {
            "tmax": tmax, "tmin": tmin, "tmean": tmean,
            "rain": rain, "pv_kwh": pv_kwh, "soc_mean": soc_mean,
            "fire": fire_series,
        },
    }


def _safe_series(*args, **kwargs) -> dict:
    """db.series that degrades to an empty result if InfluxDB is unreachable."""
    try:
        return db.series(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        log.debug("series query failed: %s", exc)
        return {}


def _soc_at_hours(sid: str, days: int, tz) -> tuple[float | None, float | None]:
    """Average SOC in the 17–19 h (sunset) and 5–7 h (sunrise) local windows."""
    s = _safe_series(config.BUCKET_ENERGY, "energy", ["battery_soc"], site_id=sid, days=days, every="1h")
    sunset, sunrise = [], []
    for t, v in zip(s.get("time", []), s.get("battery_soc", [])):
        if not isinstance(v, (int, float)):
            continue
        try:
            dt = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(tz)
        except (ValueError, AttributeError):
            continue
        if 17 <= dt.hour <= 19:
            sunset.append(v)
        elif 5 <= dt.hour <= 7:
            sunrise.append(v)
    return _mean(sunset), _mean(sunrise)


def _night_consumption(sid: str, days: int, tz) -> float | None:
    """Average nightly consumption (kWh): mean load power in local night hours
    (20:00–06:00) extrapolated over the ~10 h night."""
    s = _safe_series(config.BUCKET_ENERGY, "energy", ["load_power"], site_id=sid, days=days, every="1h")
    night = []
    for t, v in zip(s.get("time", []), s.get("load_power", [])):
        if not isinstance(v, (int, float)):
            continue
        try:
            dt = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(tz)
        except (ValueError, AttributeError):
            continue
        if dt.hour >= 20 or dt.hour < 6:
            night.append(v)
    avg_w = _mean(night)
    return avg_w * 10.0 / 1000.0 if avg_w is not None else None


# ── matplotlib charts ───────────────────────────────────────────────────────
def _fig_to_image(fig: Figure, width_cm: float = 16.0) -> RLImage:
    """Render a matplotlib figure to a reportlab Image (PNG, keeping aspect)."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    w, h = fig.get_size_inches()
    width = width_cm * cm
    return RLImage(buf, width=width, height=width * (h / w))


def _short_days(span: list[str]) -> list[str]:
    return [f"{d[8:10]}.{d[5:7]}." for d in span]


def _style_axes(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(labelsize=7, colors=DIM)
    ax.grid(axis="y", color="#e6e6f0", linewidth=0.6)
    ax.set_axisbelow(True)


def build_charts(data: dict) -> list[tuple[str, RLImage]]:
    """Build the five report charts; skip any with no data. Returns (title, img)."""
    span = data["span"]
    labels = _short_days(span)
    ser = data["series"]
    charts: list[tuple[str, RLImage]] = []

    # 1) Temperature min/max band + mean line.
    if ser["tmax"] or ser["tmin"]:
        fig, ax = plt.subplots(figsize=(8, 2.6))
        lo = [ser["tmin"].get(d) for d in span]
        hi = [ser["tmax"].get(d) for d in span]
        mean = [ser["tmean"].get(d) for d in span]
        xs = list(range(len(span)))
        band = [(i, l, h) for i, l, h in zip(xs, lo, hi) if l is not None and h is not None]
        if band:
            bx = [b[0] for b in band]
            ax.fill_between(bx, [b[1] for b in band], [b[2] for b in band],
                            color=ACCENT, alpha=0.18, label="Min–Max")
        ax.plot(xs, mean, color=ACCENT, linewidth=1.6, label="Ø")
        ax.set_xticks(xs); ax.set_xticklabels(labels, rotation=0)
        ax.set_ylabel("°C", fontsize=7, color=DIM)
        _style_axes(ax); ax.legend(fontsize=6, frameon=False)
        charts.append(("temp", _fig_to_image(fig)))

    # 2) Precipitation bars.
    if any(ser["rain"].get(d) for d in span):
        fig, ax = plt.subplots(figsize=(8, 2.2))
        ax.bar(range(len(span)), [ser["rain"].get(d, 0.0) for d in span], color="#4895ef")
        ax.set_xticks(range(len(span))); ax.set_xticklabels(labels)
        ax.set_ylabel("mm", fontsize=7, color=DIM)
        _style_axes(ax)
        charts.append(("rain", _fig_to_image(fig)))

    # 3) PV yield per day.
    if any(ser["pv_kwh"].get(d) for d in span):
        fig, ax = plt.subplots(figsize=(8, 2.2))
        ax.bar(range(len(span)), [ser["pv_kwh"].get(d, 0.0) for d in span], color="#f4a261")
        ax.set_xticks(range(len(span))); ax.set_xticklabels(labels)
        ax.set_ylabel("kWh", fontsize=7, color=DIM)
        _style_axes(ax)
        charts.append(("pv", _fig_to_image(fig)))

    # 4) SOC line.
    if any(ser["soc_mean"].get(d) is not None for d in span):
        fig, ax = plt.subplots(figsize=(8, 2.2))
        ax.plot(range(len(span)), [ser["soc_mean"].get(d) for d in span],
                color=ACCENT, linewidth=1.6, marker="o", markersize=2.5)
        ax.set_ylim(0, 100)
        ax.set_xticks(range(len(span))); ax.set_xticklabels(labels)
        ax.set_ylabel("SOC %", fontsize=7, color=DIM)
        _style_axes(ax)
        charts.append(("soc", _fig_to_image(fig)))

    # 5) Fire-danger coloured bars.
    if ser["fire"]:
        fig, ax = plt.subplots(figsize=(8, 2.2))
        vals = [ser["fire"].get(d, {}).get("ffdi", 0.0) for d in span]
        cols = [FIRE_COLORS.get(ser["fire"].get(d, {}).get("category", "low"), "#2faa5a") for d in span]
        ax.bar(range(len(span)), vals, color=cols)
        ax.set_xticks(range(len(span))); ax.set_xticklabels(labels)
        ax.set_ylabel("FFDI", fontsize=7, color=DIM)
        _style_axes(ax)
        charts.append(("fire", _fig_to_image(fig)))

    return charts


# ── PDF layout ──────────────────────────────────────────────────────────────
def _fmt(v, digits: int = 1, unit: str = "") -> str:
    if v is None:
        return "–"
    return f"{v:.{digits}f}{(' ' + unit) if unit else ''}"


CHART_TITLES = {
    "temp": "Temperaturverlauf (°C)",
    "rain": "Niederschlag (mm/Tag)",
    "pv": "PV-Ertrag (kWh/Tag)",
    "soc": "Batterie-SOC (%)",
    "fire": "Feuerrisiko (FFDI)",
}
CAT_LABELS_DE = {
    "low": "Niedrig", "moderate": "Mäßig", "high": "Hoch",
    "very_high": "Sehr hoch", "extreme": "Extrem",
}


def build_pdf(data: dict, path: str) -> str:
    """Render the gathered data + charts into a PDF at `path`. Returns `path`."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], textColor=colors.HexColor(INK), fontSize=20)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], textColor=colors.HexColor(ACCENT), fontSize=12)
    small = ParagraphStyle("small", parent=styles["Normal"], textColor=colors.HexColor(DIM), fontSize=8)

    site = data["site"]
    period_de = "Wochenbericht" if data["period"] == "weekly" else "Monatsbericht"
    span = data["span"]
    period_range = f"{span[0]} – {span[-1]}" if span else ""
    gen = data["generated_at"].strftime("%Y-%m-%d %H:%M")

    flow = [
        Paragraph(f"{period_de} · {site.get('name', site['site_id'])}", h1),
        Paragraph(f"Zeitraum: {period_range} &nbsp;·&nbsp; erstellt am {gen}", small),
        Spacer(1, 0.5 * cm),
    ]

    def section(title: str, rows: list[list[str]]):
        flow.append(Paragraph(title, h2))
        tbl = Table(rows, colWidths=[7 * cm, 8.5 * cm])
        tbl.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor(DIM)),
            ("TEXTCOLOR", (1, 0), (1, -1), colors.HexColor(INK)),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#e6e6f0")),
        ]))
        flow.append(tbl)
        flow.append(Spacer(1, 0.45 * cm))

    # Weather.
    w = data["weather"]
    section("Wetter-Zusammenfassung", [
        ["Ø / Min / Max Temperatur", f"{_fmt(w['temp_avg'])} / {_fmt(w['temp_min'])} / {_fmt(w['temp_max'])} °C"],
        ["Gesamtniederschlag", _fmt(w["precip_total"], 1, "mm")],
        ["Ø Wind / Max Böe", f"{_fmt(w['wind_avg'])} / {_fmt(w['gust_max'])} km/h"],
        ["Sonnenstunden (PSH gesamt)", _fmt(w["psh_total"], 1, "h")],
        ["Ø Luftdruck", _fmt(w["pressure_avg"], 1, "hPa")],
    ])

    # Energy.
    e = data["energy"]
    if e:
        section("Energie-Zusammenfassung", [
            ["PV-Ertrag gesamt", _fmt(e["pv_total"], 1, "kWh")],
            ["Verbrauch gesamt", _fmt(e["load_total"], 1, "kWh")],
            ["Ø Tagesverbrauch", _fmt(e["consumption_avg"], 2, "kWh")],
            ["Ø Nachtverbrauch", _fmt(e["night_consumption_avg"], 2, "kWh")],
            ["Ø SOC Sonnenuntergang / -aufgang", f"{_fmt(e['soc_sunset_avg'], 0)} / {_fmt(e['soc_sunrise_avg'], 0)} %"],
            ["Niedrigster SOC", _fmt(e["soc_min"], 0, "%")],
            ["Ø Tagesbilanz (PV − Verbrauch)", _fmt(e["balance_avg"], 2, "kWh")],
        ])

    # Fire.
    f = data["fire"]
    if f:
        cats = f["categories"]
        cat_str = ", ".join(f"{CAT_LABELS_DE.get(k, k)}: {v}"
                            for k, v in sorted(cats.items(), key=lambda kv: -kv[1]))
        section("Feuerrisiko", [
            ["Max. FFDI im Zeitraum", _fmt(f["max_ffdi"], 1)],
            ["Tage je Kategorie", cat_str or "–"],
            ["Längste Trockenperiode", f"{f['longest_dry_spell']} Tage"],
        ])

    # Microclimate.
    mc = data["microclimate"]
    if mc and mc.get("active"):
        section("Mikroklima", [
            ["Trefferquote Regen", _fmt((mc.get("rain_forecast_accuracy") or 0) * 100, 0, "%")
                if mc.get("rain_forecast_accuracy") is not None else "–"],
            ["Ø Temperatur-Abweichung", _fmt(mc.get("avg_temp_deviation"), 1, "°C")],
            ["Typische Wind-Korrektur", f"×{_fmt(mc.get('typical_wind_correction'), 2)}"],
            ["Tage Vergleichsdaten", str(mc.get("days_of_data", 0))],
        ])
    elif mc is not None:
        section("Mikroklima", [
            ["Status", f"Lernphase – {mc.get('days_of_data', 0)}/{mc.get('days_needed', 30)} Tage"],
        ])

    # Charts.
    flow.append(Paragraph("Diagramme", h2))
    for key, img in build_charts(data):
        flow.append(Paragraph(CHART_TITLES.get(key, key), small))
        flow.append(img)
        flow.append(Spacer(1, 0.3 * cm))

    doc = SimpleDocTemplate(
        path, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.6 * cm, bottomMargin=1.6 * cm,
        title=f"{period_de} {site.get('name', '')}",
    )
    doc.build(flow)
    log.info("report written: %s", path)
    return path


# ── on-disk cache ───────────────────────────────────────────────────────────
def reports_dir() -> str:
    return os.path.join(config.DATA_DIR, "reports")


def latest_path(site_id: str, period: str) -> str:
    return os.path.join(reports_dir(), f"{site_id}-{period}-latest.pdf")


# ── public entry points ─────────────────────────────────────────────────────
def generate(site_id: str | None = None, period: str = "weekly",
             output: str | None = None) -> str:
    """Generate the PDF for one site and return its path."""
    if period not in PERIODS:
        raise ValueError(f"unknown period '{period}' (weekly|monthly)")
    site = settings_store.get_site(site_id)
    if site is None:
        raise RuntimeError("no site configured")
    data = gather(site, period)
    path = output or latest_path(site["site_id"], period)
    return build_pdf(data, path)


def send_email(pdf_path: str, site: dict, period: str, recipient: str) -> None:
    """E-mail the PDF over SMTP. Raises on transport errors."""
    if not config.smtp_enabled():
        raise RuntimeError("SMTP not configured (SMTP_HOST / REPORT_EMAIL_FROM)")
    if not recipient:
        raise RuntimeError("no recipient configured")

    period_de = "Wochenbericht" if period == "weekly" else "Monatsbericht"
    name = site.get("name", site["site_id"])
    today = date.today().isoformat()

    msg = EmailMessage()
    msg["Subject"] = f"{period_de} {name} – {today}"
    msg["From"] = config.REPORT_EMAIL_FROM
    msg["To"] = recipient
    msg.set_content(
        f"Anbei der {period_de} für {name} ({today}).\n\n"
        "Weather Off-Grid – automatisch generierter Bericht."
    )
    with open(pdf_path, "rb") as fh:
        msg.add_attachment(fh.read(), maintype="application", subtype="pdf",
                           filename=os.path.basename(pdf_path))

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
        if config.SMTP_STARTTLS:
            smtp.starttls(context=ssl.create_default_context())
        if config.SMTP_USER:
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.send_message(msg)
    log.info("report e-mailed to %s for site %s", recipient, site["site_id"])


def generate_and_send(site_id: str, period: str, recipient: str) -> str:
    """Generate the cached PDF for a site and e-mail it. Returns the path."""
    path = generate(site_id, period)
    send_email(path, settings_store.get_site(site_id), period, recipient)
    return path


# ── background scheduler ────────────────────────────────────────────────────
def _next_is_due(now_local: datetime, schedule: str) -> bool:
    """True when `now_local` is in the firing window (Mon 08:xx weekly, 1st 08:xx
    monthly). The scheduler de-duplicates by persisted last-sent date."""
    if now_local.hour != 8:
        return False
    if schedule == "weekly":
        return now_local.weekday() == 0      # Monday
    if schedule == "monthly":
        return now_local.day == 1
    return False


def _state_path() -> str:
    return os.path.join(reports_dir(), "scheduler_state.json")


def _load_last_sent() -> dict:
    import json
    try:
        with open(_state_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


def _save_last_sent(state: dict) -> None:
    import json
    os.makedirs(reports_dir(), exist_ok=True)
    with open(_state_path(), "w", encoding="utf-8") as fh:
        json.dump(state, fh)


async def run_scheduler() -> None:
    """Background task: e-mail every enabled site on the configured cadence.

    Wakes every ~30 min, checks the firing window against the default site's
    timezone, and uses a persisted last-sent date so a report is sent at most
    once per due day even across restarts.
    """
    import asyncio

    log.info("report scheduler started")
    while True:
        try:
            cfg = settings_store.reports_config()
            schedule = cfg.get("schedule", "weekly")
            if cfg.get("enabled") and config.smtp_enabled() and schedule in ("weekly", "monthly"):
                default = settings_store.get_site(None) or {}
                tz = _tz(default)
                now_local = datetime.now(tz)
                marker = now_local.date().isoformat()
                last = _load_last_sent()
                if _next_is_due(now_local, schedule) and last.get(schedule) != marker:
                    recipient = cfg.get("email_to") or config.REPORT_EMAIL_TO
                    await asyncio.to_thread(_run_all_sites, schedule, recipient)
                    last[schedule] = marker
                    _save_last_sent(last)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("report scheduler error: %s", exc)
        await asyncio.sleep(1800)  # 30 min


def _run_all_sites(period: str, recipient: str) -> None:
    """Generate + e-mail a report for every configured site (blocking)."""
    for site in settings_store.sites():
        try:
            generate_and_send(site["site_id"], period, recipient)
        except Exception as exc:  # noqa: BLE001
            log.warning("report for site %s failed: %s", site.get("site_id"), exc)


# ── CLI ─────────────────────────────────────────────────────────────────────
def _main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Generate a Weather Off-Grid PDF report.")
    parser.add_argument("--site", default=None, help="site_id (default: the configured default site)")
    parser.add_argument("--period", default="weekly", choices=list(PERIODS), help="weekly | monthly")
    parser.add_argument("--output", default=None, help="output PDF path (default: data/reports/<site>-<period>-latest.pdf)")
    parser.add_argument("--email", default=None, help="also e-mail the PDF to this address")
    args = parser.parse_args()

    settings_store.load()
    path = generate(args.site, args.period, args.output)
    print(f"report written: {path}")
    if args.email:
        site = settings_store.get_site(args.site)
        send_email(path, site, args.period, args.email)
        print(f"e-mailed to {args.email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
