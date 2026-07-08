"""Tests for the Zambretti barometric forecast (pure module, no I/O)."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forecast import barometric  # noqa: E402


# ── classify_trend ──────────────────────────────────────────────────────────

def test_classify_trend():
    assert barometric.classify_trend(None) == ("steady", "→")
    assert barometric.classify_trend(0.4) == ("steady", "→")
    assert barometric.classify_trend(1.0) == ("rising", "↑")
    assert barometric.classify_trend(-1.3) == ("falling", "↓")


# ── trend_3h ────────────────────────────────────────────────────────────────

def _mk_series(hours_and_values):
    base = datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc)
    times = [(base + timedelta(hours=h)).isoformat() for h, _ in hours_and_values]
    return times, [v for _, v in hours_and_values]


def test_trend_3h_basic():
    times, values = _mk_series([(0, 1010.0), (1, 1011.0), (2, 1012.0), (3, 1013.5)])
    assert barometric.trend_3h(times, values) == 3.5


def test_trend_3h_not_enough_data():
    times, values = _mk_series([(0, 1010.0)])
    assert barometric.trend_3h(times, values) is None
    # Two readings only 30 min apart: no reference at least 1 h back.
    base = datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc)
    times = [base.isoformat(), (base + timedelta(minutes=30)).isoformat()]
    assert barometric.trend_3h(times, [1010.0, 1011.0]) is None


def test_trend_3h_anchor_is_now_end():
    # Series ends at +3 h with 1013.0; the live anchor at +4 h reads 1014.0.
    times, values = _mk_series([(0, 1010.0), (1, 1011.0), (2, 1012.0), (3, 1013.0)])
    base = datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc)
    anchor_t = base + timedelta(hours=4)
    delta = barometric.trend_3h(times, values, anchor_time=anchor_t, anchor_value=1014.0)
    # 3 h before the anchor is +1 h (1011.0): 1014.0 - 1011.0 = 3.0.
    assert delta == 3.0
    # Without the anchor the delta ends at the last series point instead:
    # 1013.0 (at +3 h) minus 1010.0 (at 0 h, nearest to 3 h back) = 3.0.
    assert barometric.trend_3h(times, values) == 3.0


# ── wind correction ─────────────────────────────────────────────────────────

def test_wind_offset_northern():
    # North wind, northern hemisphere: +6 hPa on the 100-hPa dial.
    assert barometric._wind_pressure_offset(0, southern=False) == 6.0
    # South wind is the strongest negative correction.
    assert barometric._wind_pressure_offset(180, southern=False) == -12.0


def test_wind_offset_southern_rotated():
    # Southern hemisphere rotates 180°: a southerly there behaves like a northerly.
    assert barometric._wind_pressure_offset(180, southern=True) == 6.0
    assert barometric._wind_pressure_offset(0, southern=True) == -12.0


def test_wind_offset_calm_skipped():
    assert barometric._wind_pressure_offset(180, southern=False, wind_speed=0.5) == 0.0
    assert barometric._wind_pressure_offset(180, southern=False, wind_speed=10.0) == -12.0


def test_wind_offset_none():
    assert barometric._wind_pressure_offset(None, southern=False) == 0.0


# ── season ──────────────────────────────────────────────────────────────────

def test_season():
    assert barometric._is_local_summer(7, southern=False) is True
    assert barometric._is_local_summer(7, southern=True) is False
    assert barometric._is_local_summer(1, southern=True) is True
    assert barometric._is_local_summer(1, southern=False) is False


# ── zambretti ───────────────────────────────────────────────────────────────

def test_zambretti_none_without_pressure():
    assert barometric.zambretti(None, "steady", None, 7) is None


def test_zambretti_high_pressure_rising_is_settled():
    z = barometric.zambretti(1035.0, "rising", None, 1, southern=False)
    assert z["index"] == 0
    assert z["letter"] == "A"
    assert z["severity"] == "good"
    assert z["rain_likely"] is False


def test_zambretti_low_pressure_falling_is_stormy():
    z = barometric.zambretti(960.0, "falling", None, 7, southern=False)
    assert z["index"] == 25
    assert z["letter"] == "Z"
    assert z["severity"] == "deteriorating"
    assert z["rain_likely"] is True


def test_zambretti_season_correction_applied():
    # Winter (N hemisphere, Jan): no seasonal shift.
    z_winter = barometric.zambretti(1002.0, "rising", None, 1, southern=False)
    # Summer (N hemisphere, Jul): rising reads +7 hPa → better forecast.
    z_summer = barometric.zambretti(1002.0, "rising", None, 7, southern=False)
    assert z_summer["pressure_effective"] == z_winter["pressure_effective"] + 7.0
    assert z_summer["index"] <= z_winter["index"]
    assert z_winter["season_summer"] is False
    assert z_summer["season_summer"] is True


def test_zambretti_season_swap_southern():
    # January is summer down south: same shift as July up north.
    z = barometric.zambretti(1002.0, "falling", None, 1, southern=True)
    assert z["season_summer"] is True
    assert z["pressure_effective"] == 995.0


def test_zambretti_effective_pressure_clamped():
    z = barometric.zambretti(1080.0, "rising", 0, 7, southern=False, wind_speed=10)
    assert z["pressure_effective"] == barometric.Z_TOP
    assert z["index"] == 0
    z = barometric.zambretti(900.0, "falling", 180, 1, southern=False, wind_speed=10)
    assert z["pressure_effective"] == barometric.Z_BOTTOM
    assert z["index"] == 25


def test_zambretti_wind_worsens_forecast():
    calm = barometric.zambretti(1005.0, "falling", None, 1, southern=False)
    southerly = barometric.zambretti(1005.0, "falling", 180, 1, southern=False, wind_speed=15)
    assert southerly["pressure_effective"] == calm["pressure_effective"] - 12.0
    assert southerly["index"] >= calm["index"]


def test_zambretti_unknown_trend_falls_back_to_steady():
    a = barometric.zambretti(1005.0, "steady", None, 1, southern=False)
    b = barometric.zambretti(1005.0, "bogus", None, 1, southern=False)
    assert a["index"] == b["index"]
