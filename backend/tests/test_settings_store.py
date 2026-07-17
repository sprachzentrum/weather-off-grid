"""Settings store: site resolution, validation, secret masking, webhook routing."""
import hashlib

import pytest

import settings_store
from conftest import make_site, make_state


# ── slugify ──────────────────────────────────────────────────────────────────
def test_slugify():
    assert settings_store.slugify("El Durazno") == "el-durazno"
    assert settings_store.slugify("Grünes Tal / Süd") == "gruenes-tal-sued"
    assert settings_store.slugify("!!!") == "site"
    assert settings_store.slugify("") == "site"


# ── get_site ─────────────────────────────────────────────────────────────────
def test_get_site_default_and_known(temp_settings):
    settings_store.save_state(make_state([make_site("alpha"), make_site("beta")]))
    assert settings_store.get_site(None)["site_id"] == "alpha"
    assert settings_store.get_site("beta")["site_id"] == "beta"


def test_get_site_unknown_id_returns_none(temp_settings):
    """An explicit but unknown id must NOT fall back to the default site."""
    settings_store.save_state(make_state([make_site("alpha")]))
    assert settings_store.get_site("does-not-exist") is None


def test_get_site_no_sites(temp_settings):
    settings_store.save_state(make_state([]))
    assert settings_store.get_site(None) is None


# ── validation ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("field,value", [
    ("latitude", 91), ("latitude", -100), ("longitude", 200),
    ("pv_efficiency", 1.5), ("pv_efficiency", -0.1),
    ("battery_capacity_kwh", "abc"),
])
def test_upsert_rejects_out_of_range(temp_settings, field, value):
    settings_store.save_state(make_state([make_site("alpha")]))
    with pytest.raises(ValueError):
        settings_store.upsert_site(make_site("alpha", **{field: value}))


def test_upsert_rejects_bad_timezone(temp_settings):
    settings_store.save_state(make_state([make_site("alpha")]))
    with pytest.raises(ValueError):
        settings_store.upsert_site(make_site("alpha", timezone='X") |> evil()'))


def test_site_id_is_always_a_slug(temp_settings):
    settings_store.save_state(make_state([make_site("alpha")]))
    saved = settings_store.upsert_site(make_site('Nasty "Id"/../x', name="Nasty"))
    assert settings_store.SITE_ID_RE.match(saved["site_id"])


def test_apply_update_invalid_leaves_state_untouched(temp_settings):
    settings_store.save_state(make_state([make_site("alpha", latitude=-32.15)]))
    with pytest.raises(ValueError):
        settings_store.apply_update({
            "display": {"language": "en"},
            "sites": [make_site("alpha", latitude=999)],
        })
    st = settings_store.load()
    assert st["sites"][0]["latitude"] == -32.15
    assert st["display"]["language"] == "de"


# ── masking ──────────────────────────────────────────────────────────────────
def test_mask_site_hides_all_secrets():
    site = make_site("alpha",
                     growatt={"password": "gw-secret"},
                     ecowitt={"app_key": "app-secret", "api_key": "api-secret"})
    masked = settings_store.mask_site(site)
    assert masked["growatt"]["password"] == settings_store.MASK
    assert masked["ecowitt"]["app_key"] == settings_store.MASK
    assert masked["ecowitt"]["api_key"] == settings_store.MASK
    # non-secrets survive
    assert masked["ecowitt"]["mac"] == site["ecowitt"]["mac"]


def test_unmask_site_restores_stored_secrets():
    stored = make_site("alpha",
                       growatt={"password": "gw-secret"},
                       ecowitt={"app_key": "app-secret", "api_key": "api-secret"})
    incoming = settings_store.mask_site(stored)
    restored = settings_store.unmask_site(incoming, stored)
    assert restored["growatt"]["password"] == "gw-secret"
    assert restored["ecowitt"]["app_key"] == "app-secret"
    assert restored["ecowitt"]["api_key"] == "api-secret"


def test_unmask_site_accepts_new_secrets():
    stored = make_site("alpha", growatt={"password": "old"})
    incoming = make_site("alpha", growatt={"password": "new"},
                         ecowitt={"app_key": "fresh"})
    restored = settings_store.unmask_site(incoming, stored)
    assert restored["growatt"]["password"] == "new"
    assert restored["ecowitt"]["app_key"] == "fresh"


def test_upsert_roundtrip_keeps_masked_secrets(temp_settings):
    settings_store.save_state(make_state([
        make_site("alpha", growatt={"password": "gw-secret"},
                  ecowitt={"api_key": "api-secret"})]))
    masked = settings_store.mask_site(settings_store.get_site("alpha"))
    settings_store.upsert_site(masked)
    saved = settings_store.get_site("alpha")
    assert saved["growatt"]["password"] == "gw-secret"
    assert saved["ecowitt"]["api_key"] == "api-secret"


# ── ecowitt webhook routing ──────────────────────────────────────────────────
MAC = "C4:5B:BE:6E:46:15"


def test_site_for_ecowitt_matches_mac(temp_settings):
    settings_store.save_state(make_state([
        make_site("alpha", ecowitt={"mac": MAC}),
        make_site("beta"),
    ]))
    site = settings_store.site_for_ecowitt("c45bbe6e4615", None)
    assert site and site["site_id"] == "alpha"


def test_site_for_ecowitt_matches_passkey(temp_settings):
    settings_store.save_state(make_state([make_site("alpha", ecowitt={"mac": MAC})]))
    passkey = hashlib.md5("C45BBE6E4615".encode()).hexdigest().upper()
    site = settings_store.site_for_ecowitt(None, passkey)
    assert site and site["site_id"] == "alpha"


def test_site_for_ecowitt_rejects_unknown_station(temp_settings):
    """With a MAC configured, a non-matching payload must be rejected (None)."""
    settings_store.save_state(make_state([make_site("alpha", ecowitt={"mac": MAC})]))
    assert settings_store.site_for_ecowitt("00:11:22:33:44:55", None) is None
    assert settings_store.site_for_ecowitt(None, "0" * 32) is None
    assert settings_store.site_for_ecowitt(None, None) is None


def test_site_for_ecowitt_first_run_fallback(temp_settings):
    """With NO station MAC configured anywhere, the default site accepts data."""
    settings_store.save_state(make_state([make_site("alpha")]))
    site = settings_store.site_for_ecowitt("00:11:22:33:44:55", None)
    assert site and site["site_id"] == "alpha"


def test_site_timezone_name(temp_settings):
    settings_store.save_state(make_state([make_site("alpha", timezone="Europe/Berlin")]))
    assert settings_store.site_timezone_name("alpha") == "Europe/Berlin"
    # unknown id falls back to the default site's tz
    assert settings_store.site_timezone_name("nope") == "Europe/Berlin"
