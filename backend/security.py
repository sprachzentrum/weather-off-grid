"""
Admin authentication helpers.

The settings/admin API is protected by a PIN. Resolution order:

  1. ADMIN_PIN from the environment (.env) - explicit operator choice.
  2. If empty and ADMIN_ALLOW_OPEN=true - the admin API runs unprotected.
     This is an explicit development/trusted-network opt-in only.
  3. Otherwise a random PIN is generated on first start and persisted to
     DATA_DIR/admin_pin.txt (mode 0600) so the API is never silently open.
     The operator reads the PIN from that file (the value itself is never
     logged).

PIN checks are constant-time and failed attempts are rate-limited per client
address so the PIN cannot be brute-forced.
"""
from __future__ import annotations

import logging
import os
import secrets
import threading
import time

import config

log = logging.getLogger("security")

GENERATED_PIN_FILE = "admin_pin.txt"

_lock = threading.Lock()
_cached_pin: str | None = None


def _pin_path() -> str:
    return os.path.join(config.DATA_DIR, GENERATED_PIN_FILE)


def _load_or_create_pin() -> str:
    """Read the generated PIN from DATA_DIR, creating it on first start."""
    path = _pin_path()
    try:
        with open(path, encoding="utf-8") as fh:
            pin = fh.read().strip()
        if pin:
            return pin
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.error("cannot read generated admin PIN (%s): %s", path, exc)

    pin = secrets.token_hex(5)  # 10 hex chars, ~40 bit
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(pin + "\n")
        log.warning(
            "no ADMIN_PIN configured - generated one and stored it in %s "
            "(set ADMIN_PIN in .env or ADMIN_ALLOW_OPEN=true to override)",
            path,
        )
    except OSError as exc:
        # Cannot persist (read-only volume?): keep the in-memory PIN for this
        # process rather than leaving the admin API open.
        log.error("cannot persist generated admin PIN (%s): %s", path, exc)
    return pin


def effective_admin_pin() -> str:
    """The PIN protecting the admin API ("" only with ADMIN_ALLOW_OPEN)."""
    global _cached_pin
    if config.ADMIN_PIN:
        return config.ADMIN_PIN
    if config.ADMIN_ALLOW_OPEN:
        return ""
    with _lock:
        if _cached_pin is None:
            _cached_pin = _load_or_create_pin()
        return _cached_pin


def pin_required() -> bool:
    return bool(effective_admin_pin())


def check_pin(pin: str | None) -> bool:
    """Constant-time PIN comparison. True when no PIN protection is active."""
    expected = effective_admin_pin()
    if not expected:
        return True
    if not isinstance(pin, str) or not pin:
        return False
    return secrets.compare_digest(pin, expected)


def reset_for_test() -> None:
    global _cached_pin
    with _lock:
        _cached_pin = None


# ── rate limiting ────────────────────────────────────────────────────────────
class PinRateLimiter:
    """Per-client failure counter: after MAX_FAILURES failed PIN checks within
    WINDOW seconds the client is locked out for LOCKOUT seconds."""

    MAX_FAILURES = 5
    WINDOW = 300.0
    LOCKOUT = 300.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # client -> (failure timestamps, locked_until)
        self._state: dict[str, tuple[list[float], float]] = {}

    def blocked(self, client: str) -> bool:
        now = time.monotonic()
        with self._lock:
            fails, locked_until = self._state.get(client, ([], 0.0))
            return now < locked_until

    def register_failure(self, client: str) -> None:
        now = time.monotonic()
        with self._lock:
            fails, locked_until = self._state.get(client, ([], 0.0))
            fails = [t for t in fails if now - t < self.WINDOW]
            fails.append(now)
            if len(fails) >= self.MAX_FAILURES:
                locked_until = now + self.LOCKOUT
                log.warning("admin PIN: too many failures from %s - locked for %ds",
                            client, int(self.LOCKOUT))
            self._state[client] = (fails, locked_until)

    def register_success(self, client: str) -> None:
        with self._lock:
            self._state.pop(client, None)

    def reset(self) -> None:
        with self._lock:
            self._state.clear()


limiter = PinRateLimiter()
