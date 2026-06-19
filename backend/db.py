"""
Thin wrapper around the InfluxDB 2.x client.

Provides a shared client, bucket bootstrapping, a simple write helper and a few
query helpers used by the API endpoints. All collectors and endpoints go through
this module so connection handling lives in one place.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable

from influxdb_client import InfluxDBClient, Point, BucketRetentionRules
from influxdb_client.client.write_api import SYNCHRONOUS

import config
import settings_store

log = logging.getLogger("db")

_client: InfluxDBClient | None = None


def get_client() -> InfluxDBClient:
    """Return a lazily-created, process-wide InfluxDB client."""
    global _client
    if _client is None:
        _client = InfluxDBClient(
            url=config.INFLUXDB_URL,
            token=config.INFLUXDB_TOKEN,
            org=config.INFLUXDB_ORG,
            timeout=20_000,
        )
    return _client


def ensure_buckets() -> None:
    """
    Create the weather/energy/forecasts buckets if they do not exist yet.

    InfluxDB's docker init only creates the first bucket, so we create the rest
    here on startup. Idempotent: existing buckets are left untouched.
    """
    client = get_client()
    buckets_api = client.buckets_api()
    org = config.INFLUXDB_ORG
    wanted = [
        config.BUCKET_WEATHER,
        config.BUCKET_ENERGY,
        config.BUCKET_FORECASTS,
    ]
    for name in wanted:
        try:
            existing = buckets_api.find_bucket_by_name(name)
        except Exception:  # noqa: BLE001 - find raises on some versions if missing
            existing = None
        if existing is not None:
            log.info("bucket '%s' already exists", name)
            continue
        # retention 0 = keep forever (this is long-term climate data)
        buckets_api.create_bucket(
            bucket_name=name,
            org=org,
            retention_rules=BucketRetentionRules(type="expire", every_seconds=0),
        )
        log.info("created bucket '%s'", name)


def health() -> bool:
    """True if InfluxDB answers its readiness probe."""
    try:
        h = get_client().health()
        return h.status == "pass"
    except Exception as exc:  # noqa: BLE001
        log.warning("influx health check failed: %s", exc)
        return False


def write_point(
    bucket: str,
    measurement: str,
    fields: dict[str, Any],
    tags: dict[str, str] | None = None,
    ts: datetime | None = None,
) -> None:
    """
    Write a single measurement point. Non-numeric / None fields are dropped so a
    partial sensor payload never aborts the whole write.
    """
    point = Point(measurement)
    for key, value in (tags or {}).items():
        if value is not None and value != "":
            point.tag(key, str(value))

    wrote_any = False
    for key, value in fields.items():
        if value is None:
            continue
        point.field(key, value)
        wrote_any = True
    if not wrote_any:
        return

    point.time(ts or datetime.now(timezone.utc))
    get_client().write_api(write_options=SYNCHRONOUS).write(
        bucket=bucket, org=config.INFLUXDB_ORG, record=point
    )


def write_points(bucket: str, points: Iterable[Point]) -> None:
    """Batch-write pre-built Point objects (used by the historical importer)."""
    get_client().write_api(write_options=SYNCHRONOUS).write(
        bucket=bucket, org=config.INFLUXDB_ORG, record=list(points)
    )


def query(flux: str) -> list:
    """Run a Flux query and return the raw FluxTable list."""
    return get_client().query_api().query(flux, org=config.INFLUXDB_ORG)


def _site_filter(site_id: str | None) -> str:
    """
    Optional Flux line restricting to one site. Empty when site_id is None.

    The DEFAULT site also claims points without a site_id tag, so data imported
    before site tagging existed (historical Ecowitt/Open-Meteo imports) still
    shows up under the default site. Non-default sites require an exact tag match.
    """
    if not site_id:
        return ""
    try:
        is_default = site_id == settings_store.default_site_id()
    except Exception:  # noqa: BLE001
        is_default = False
    if is_default:
        return f'\n      |> filter(fn: (r) => r.site_id == "{site_id}" or not exists r.site_id)'
    return f'\n      |> filter(fn: (r) => r.site_id == "{site_id}")'


def latest_fields(
    bucket: str, measurement: str, site_id: str | None = None, since: str = "-2h"
) -> dict[str, Any]:
    """
    Return the most recent value of every field of a measurement as a flat dict.
    Empty dict if there is no data in the window. Restricted to one site when
    site_id is given.
    """
    flux = f'''
    from(bucket: "{bucket}")
      |> range(start: {since})
      |> filter(fn: (r) => r._measurement == "{measurement}"){_site_filter(site_id)}
      |> last()
    '''
    out: dict[str, Any] = {}
    for table in query(flux):
        for record in table.records:
            out[record.get_field()] = record.get_value()
            out.setdefault("_time", record.get_time())
    return out


def series(
    bucket: str,
    measurement: str,
    fields: list[str],
    site_id: str | None = None,
    days: int = 7,
    every: str = "1h",
) -> dict[str, list]:
    """
    Aggregated time series for charts: returns {"time": [...], field: [...], ...}
    using mean aggregation over `every` windows for the last `days` days.
    Restricted to one site when site_id is given.
    """
    field_filter = " or ".join(f'r._field == "{f}"' for f in fields)
    flux = f'''
    from(bucket: "{bucket}")
      |> range(start: -{days}d)
      |> filter(fn: (r) => r._measurement == "{measurement}"){_site_filter(site_id)}
      |> filter(fn: (r) => {field_filter})
      |> aggregateWindow(every: {every}, fn: mean, createEmpty: false)
      |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> sort(columns: ["_time"])
    '''
    result: dict[str, list] = {"time": []}
    for f in fields:
        result[f] = []
    for table in query(flux):
        for record in table.records:
            result["time"].append(record.get_time().isoformat())
            values = record.values
            for f in fields:
                result[f].append(values.get(f))
    return result


def count_points(bucket: str, site_id: str | None = None, days: int = 365) -> int:
    """Approximate number of stored points in a bucket (optionally per site)."""
    flux = f'''
    from(bucket: "{bucket}")
      |> range(start: -{days}d){_site_filter(site_id)}
      |> filter(fn: (r) => exists r._value)
      |> count()
      |> sum()
    '''
    try:
        total = 0
        for table in query(flux):
            for record in table.records:
                total += int(record.get_value() or 0)
        return total
    except Exception as exc:  # noqa: BLE001
        log.debug("count_points failed: %s", exc)
        return 0


def close() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
