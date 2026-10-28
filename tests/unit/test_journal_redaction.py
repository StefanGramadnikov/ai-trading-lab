"""What goes into the error journal is stripped of credentials and bounded in size."""

import pytest

from ai_trading_lab.live.journal import MAX_TRACE, _location, _trace, fingerprint, redact


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("auth failed for sk-proj-AbCdEf123456789xyz", "AbCdEf123456789xyz"),
        ("Authorization: Bearer abcdefghij.klmnop-12345", "abcdefghij"),
        ("api_key=SuperSecretValue123 rejected", "SuperSecretValue123"),
        ("'password': 'hunter2hunter2'", "hunter2hunter2"),
        ("token: abcDEF123456", "abcDEF123456"),
        (
            "could not connect to postgresql+psycopg://user:s3cretpw@db:5432/ai_trading_lab",
            "s3cretpw",
        ),
        ("pw is 0123456789abcdef0123456789abcdef0123456789abcdef", "0123456789abcdef0123456789"),
        ("blob QWxhZGRpbjpvcGVuIHNlc2FtZVRoaXNJc0Fsb25nVG9rZW5WYWx1ZTEyMzQ1Ng", "QWxhZGRpbjpvcGVu"),
        ("x-api-key: kkkkkkkkkkkk", "kkkkkkkkkkkk"),
    ],
)
def test_credentials_are_removed(raw: str, secret: str) -> None:
    cleaned = redact(raw)
    assert secret not in cleaned
    assert "redacted" in cleaned


def test_ordinary_text_survives() -> None:
    text = "TypeError: unsupported operand for Decimal in live.strategies:decide_rules at cycle 7"
    assert redact(text) == text
    uuid = "call 8b0c5c9d-4ae3-5978-8d01-c1aeb91cb7f9 failed"
    assert redact(uuid) == uuid  # ids with dashes are how a problem is found in the logs


def _boom_a() -> None:
    raise ValueError("password=abcdef123456 was wrong")


def _boom_b() -> None:
    raise ValueError("password=abcdef123456 was wrong")


def test_the_fingerprint_names_the_place_not_the_line_and_the_trace_is_clean() -> None:
    caught: list[BaseException] = []
    for function in (_boom_a, _boom_a, _boom_b):
        try:
            function()
        except ValueError as exc:
            caught.append(exc)
    first, second, other = caught
    assert _location(first) == _location(second) != "unknown"
    assert _location(first) != _location(other)
    assert fingerprint("rules_strategy", "ValueError", _location(first)) == fingerprint(
        "rules_strategy", "ValueError", _location(second)
    )
    assert fingerprint("rules_strategy", "ValueError", "x") != fingerprint(
        "ai_strategy", "ValueError", "x"
    )
    trace = _trace(first)
    assert "abcdef123456" not in trace and "ValueError" in trace and "_boom_a" in trace


def test_a_huge_trace_is_shortened_but_keeps_both_ends() -> None:
    try:
        raise RuntimeError("start " + "word " * 4000 + "end")
    except RuntimeError as exc:
        trace = _trace(exc)
    assert len(trace) <= MAX_TRACE + 10
    assert "[trace shortened]" in trace
    assert trace.startswith("Traceback") and trace.rstrip().endswith("end")
