"""Threshold alerts: pure evaluation logic + per-site threshold merging."""
import alerts
from conftest import make_site


def th(**overrides):
    return {**alerts.DEFAULTS, **overrides}


def test_no_data_no_alerts():
    assert alerts.evaluate(th(), {}, {}) == []


def test_soc_alert():
    out = alerts.evaluate(th(soc_min=20), {}, {"battery_soc": 15})
    assert [a["key"] for a in out] == ["soc"]
    assert alerts.evaluate(th(soc_min=20), {}, {"battery_soc": 20}) == []


def test_soc_check_disabled_with_none():
    assert alerts.evaluate(th(soc_min=None), {}, {"battery_soc": 1}) == []


def test_wind_alert_uses_gust_or_speed():
    out = alerts.evaluate(th(wind_max=80), {"wind_speed": 30, "wind_gust": 95}, {})
    assert [a["key"] for a in out] == ["wind"]
    assert alerts.evaluate(th(wind_max=80), {"wind_speed": 30, "wind_gust": 60}, {}) == []


def test_heat_and_cold_alerts():
    hot = alerts.evaluate(th(heat_max=38, cold_min=0), {"temperature_outdoor": 40.5}, {})
    assert [a["key"] for a in hot] == ["heat"]
    cold = alerts.evaluate(th(heat_max=38, cold_min=0), {"temperature_outdoor": -1.2}, {})
    assert [a["key"] for a in cold] == ["cold"]
    mild = alerts.evaluate(th(heat_max=38, cold_min=0), {"temperature_outdoor": 20}, {})
    assert mild == []


def test_fire_alert_respects_category_order():
    assert alerts.evaluate(th(fire_min_category="very_high"), {}, {},
                           fire_category="high") == []
    out = alerts.evaluate(th(fire_min_category="very_high"), {}, {},
                          fire_category="extreme")
    assert [a["key"] for a in out] == ["fire"]
    # "off" disables the fire check entirely
    assert alerts.evaluate(th(fire_min_category="off"), {}, {},
                           fire_category="extreme") == []


def test_multiple_alerts_at_once():
    out = alerts.evaluate(
        th(soc_min=20, wind_max=80, heat_max=38),
        {"temperature_outdoor": 39, "wind_speed": 90},
        {"battery_soc": 10},
    )
    assert sorted(a["key"] for a in out) == ["heat", "soc", "wind"]
    for a in out:
        assert a["text"] and a["label"]


def test_thresholds_for_merges_site_overrides():
    site = make_site("alpha", alerts={"soc_min": 35, "enabled": True})
    merged = alerts.thresholds_for(site)
    assert merged["soc_min"] == 35
    assert merged["enabled"] is True
    assert merged["wind_max"] == alerts.DEFAULTS["wind_max"]  # untouched default


def test_due_and_cooldown():
    alerts.reset_for_test()
    active = [{"key": "soc", "label": "x", "text": "t", "value": 1, "threshold": 2}]
    assert alerts._due("s1", active) == active
    alerts._mark_sent("s1", active)
    assert alerts._due("s1", active) == []       # within cooldown
    assert alerts._due("s2", active) == active   # other site unaffected
    alerts.reset_for_test()
