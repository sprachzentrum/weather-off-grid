"""Admin PIN resolution and brute-force rate limiting."""
import os

import config
import security


def test_env_pin_wins(temp_settings, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_PIN", "1234")
    assert security.effective_admin_pin() == "1234"
    assert security.pin_required()
    assert security.check_pin("1234")
    assert not security.check_pin("9999")
    assert not security.check_pin(None)
    assert not security.check_pin(1234)  # non-string never matches


def test_allow_open_disables_pin(temp_settings, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_PIN", "")
    monkeypatch.setattr(config, "ADMIN_ALLOW_OPEN", True)
    assert security.effective_admin_pin() == ""
    assert not security.pin_required()
    assert security.check_pin(None)


def test_pin_generated_and_persisted(temp_settings, monkeypatch):
    """Empty ADMIN_PIN without the explicit opt-out generates a persisted PIN."""
    monkeypatch.setattr(config, "ADMIN_PIN", "")
    monkeypatch.setattr(config, "ADMIN_ALLOW_OPEN", False)
    pin = security.effective_admin_pin()
    assert pin and len(pin) >= 8
    path = os.path.join(str(temp_settings), security.GENERATED_PIN_FILE)
    assert os.path.exists(path)
    assert oct(os.stat(path).st_mode & 0o777) == oct(0o600)
    # Stable across a cache reset (simulated restart): same file, same PIN.
    security.reset_for_test()
    assert security.effective_admin_pin() == pin


def test_rate_limiter_locks_after_failures():
    limiter = security.PinRateLimiter()
    client = "192.0.2.7"
    assert not limiter.blocked(client)
    for _ in range(limiter.MAX_FAILURES):
        limiter.register_failure(client)
    assert limiter.blocked(client)
    # other clients unaffected
    assert not limiter.blocked("192.0.2.8")


def test_rate_limiter_success_clears():
    limiter = security.PinRateLimiter()
    client = "192.0.2.7"
    for _ in range(limiter.MAX_FAILURES - 1):
        limiter.register_failure(client)
    limiter.register_success(client)
    for _ in range(limiter.MAX_FAILURES - 1):
        limiter.register_failure(client)
    assert not limiter.blocked(client)
