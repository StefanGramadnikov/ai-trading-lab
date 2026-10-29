"""One owner, one password, a signed cookie. No accounts, no registration.

The password is never stored: the server holds a salted scrypt hash and a random signing secret,
both in the env file. The cookie is stateless (HMAC-signed, HttpOnly, SameSite=Strict), carries a
CSRF token, and expires after an idle period and after an absolute lifetime. Logging out clears the
cookie but cannot revoke a copy someone already stole; the page is reachable only through an SSH
tunnel, which is the real access control. Failed sign-ins are throttled globally.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 200
ABSOLUTE_LIFETIME_SECONDS = 12 * 3600
COOKIE_NAME = "atl_session"


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """`scrypt:N:r:p:salt:hash` with hex fields. No `$`, so env files and Compose keep it intact."""
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise ValueError(
            f"password must be {MIN_PASSWORD_LENGTH} to {MAX_PASSWORD_LENGTH} characters"
        )
    salt = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return f"scrypt:{SCRYPT_N}:{SCRYPT_R}:{SCRYPT_P}:{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """False for a wrong password and for any malformed stored value; never raises."""
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split(":")
        if scheme != "scrypt" or not password or len(password) > MAX_PASSWORD_LENGTH:
            return False
        n_value, r_value, p_value = int(n), int(r), int(p)
        if n_value > 2**16 or r_value > 16 or p_value > 4:
            return False  # refuse parameters that would make a check expensive
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt_hex),
            n=n_value,
            r=r_value,
            p=p_value,
            dklen=len(expected),
            maxmem=128 * n_value * r_value * 2 + 1024 * 1024,
        )
    except (ValueError, TypeError, OverflowError):
        return False
    return hmac.compare_digest(actual, expected)


@dataclass(frozen=True)
class Session:
    issued_at: int
    last_seen: int
    csrf: str


class SessionSigner:
    def __init__(self, secret: bytes, *, idle_seconds: int) -> None:
        if len(secret) < 32:
            raise ValueError("session secret must be at least 32 bytes")
        self._secret = secret
        self.idle_seconds = idle_seconds

    def _sign(self, payload: str) -> str:
        mac = hmac.new(self._secret, payload.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac).decode().rstrip("=")

    def issue(self, *, now: float | None = None, previous: Session | None = None) -> str:
        """A new cookie value. Passing the previous session keeps its CSRF token and start time."""
        moment = int(time.time() if now is None else now)
        session = Session(
            issued_at=previous.issued_at if previous else moment,
            last_seen=moment,
            csrf=previous.csrf if previous else secrets.token_urlsafe(24),
        )
        body = json.dumps(
            {"i": session.issued_at, "l": session.last_seen, "c": session.csrf},
            separators=(",", ":"),
        )
        payload = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
        return f"{payload}.{self._sign(payload)}"

    def read(self, value: str | None, *, now: float | None = None) -> Session | None:
        """The session if the signature is right and neither timeout has passed; else None."""
        if not value or value.count(".") != 1 or len(value) > 512 or not value.isascii():
            return None  # compare_digest refuses non-ASCII text, so never let it get that far
        payload, signature = value.split(".")
        if not hmac.compare_digest(self._sign(payload), signature):
            return None
        try:
            body = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            session = Session(int(body["i"]), int(body["l"]), str(body["c"]))
        except (ValueError, KeyError, TypeError):
            return None
        moment = time.time() if now is None else now
        if moment - session.last_seen > self.idle_seconds:
            return None
        if moment - session.issued_at > ABSOLUTE_LIFETIME_SECONDS:
            return None
        return session


class LoginThrottle:
    """At most `limit` failed sign-ins per `window` seconds, counted for everyone together."""

    def __init__(self, limit: int = 5, window: float = 900.0) -> None:
        self.limit = limit
        self.window = window
        self._failures: list[float] = []

    def _trim(self, now: float) -> None:
        self._failures = [t for t in self._failures if now - t < self.window]

    def retry_after(self, now: float | None = None) -> int:
        """Seconds until another attempt is allowed; 0 when one is allowed now."""
        moment = time.monotonic() if now is None else now
        self._trim(moment)
        if len(self._failures) < self.limit:
            return 0
        return max(1, int(self.window - (moment - self._failures[0])) + 1)

    def failed(self, now: float | None = None) -> None:
        self._failures.append(time.monotonic() if now is None else now)

    def succeeded(self) -> None:
        self._failures.clear()
