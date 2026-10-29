"""Password hashing, the signed session cookie and the sign-in throttle."""

import pytest

from ai_trading_lab.dashboard.auth import (
    ABSOLUTE_LIFETIME_SECONDS,
    LoginThrottle,
    SessionSigner,
    hash_password,
    verify_password,
)

SECRET = b"s" * 32
PASSWORD = "correct horse battery"


def test_a_password_verifies_against_its_own_hash_only() -> None:
    stored = hash_password(PASSWORD)
    assert verify_password(PASSWORD, stored)
    assert not verify_password(PASSWORD + "x", stored)
    assert not verify_password("", stored)


def test_hashes_are_salted_and_contain_no_dollar_sign() -> None:
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second
    assert "$" not in first  # Compose would treat it as interpolation


def test_a_short_password_is_refused_when_hashing() -> None:
    with pytest.raises(ValueError, match="12"):
        hash_password("short")


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "plain",
        "scrypt:16384:8:1:zz:zz",
        "bcrypt:16384:8:1:00:00",
        "scrypt:99999999:8:1:00:00",  # absurd cost parameters are refused, not computed
        "scrypt:16384:8:1:00",
    ],
)
def test_a_malformed_stored_hash_is_a_failed_check_not_an_error(stored: str) -> None:
    assert verify_password(PASSWORD, stored) is False


def test_an_overlong_password_is_refused_without_hashing_it() -> None:
    assert verify_password("x" * 10_000, hash_password(PASSWORD)) is False


def test_a_session_round_trips_and_keeps_its_csrf_token_when_renewed() -> None:
    signer = SessionSigner(SECRET, idle_seconds=1800)
    cookie = signer.issue(now=1000)
    session = signer.read(cookie, now=1010)
    assert session is not None
    renewed = signer.read(signer.issue(now=1500, previous=session), now=1500)
    assert renewed is not None
    assert (renewed.csrf, renewed.issued_at) == (session.csrf, session.issued_at)
    assert renewed.last_seen == 1500


def test_an_idle_session_expires_but_activity_keeps_it_alive() -> None:
    signer = SessionSigner(SECRET, idle_seconds=1800)
    session = signer.read(signer.issue(now=0), now=100)
    assert session is not None
    assert signer.read(signer.issue(now=0), now=1801) is None
    kept = signer.issue(now=1700, previous=session)
    assert signer.read(kept, now=3000) is not None


def test_a_session_ends_at_the_absolute_lifetime_even_when_active() -> None:
    signer = SessionSigner(SECRET, idle_seconds=1800)
    session = signer.read(signer.issue(now=0), now=1)
    assert session is not None
    late = ABSOLUTE_LIFETIME_SECONDS + 1
    assert signer.read(signer.issue(now=late - 10, previous=session), now=late) is None


def test_a_tampered_or_foreign_cookie_is_rejected() -> None:
    signer = SessionSigner(SECRET, idle_seconds=1800)
    cookie = signer.issue(now=0)
    payload, signature = cookie.split(".")
    other = SessionSigner(b"t" * 32, idle_seconds=1800)
    for bad in (
        payload + ".AAAA",
        "x" + payload + "." + signature,
        other.issue(now=0),
        "",
        None,
        "a.b.c",
        "." + signature,
        "x" * 600,
    ):
        assert signer.read(bad, now=1) is None


def test_the_signing_secret_must_be_long_enough() -> None:
    with pytest.raises(ValueError, match="32"):
        SessionSigner(b"short", idle_seconds=60)


def test_the_throttle_blocks_after_five_failures_and_forgets_after_the_window() -> None:
    throttle = LoginThrottle(limit=5, window=900)
    for second in range(5):
        assert throttle.retry_after(second) == 0
        throttle.failed(second)
    assert throttle.retry_after(10) > 0
    assert throttle.retry_after(899) > 0
    assert (
        throttle.retry_after(901) == 0
    )  # t=0 left the window: four failures remain, one below the limit
    assert throttle.retry_after(5000) == 0


def test_a_successful_sign_in_clears_the_failures() -> None:
    throttle = LoginThrottle(limit=2, window=900)
    throttle.failed(0)
    throttle.failed(1)
    assert throttle.retry_after(2) > 0
    throttle.succeeded()
    assert throttle.retry_after(3) == 0
