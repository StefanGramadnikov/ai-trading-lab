from datetime import timedelta
from decimal import Decimal, localcontext

import pytest

from ai_trading_lab.market_data.models import DataQuality
from ai_trading_lab.paper_execution.models import ExecutionQuote, PaperInstrument
from ai_trading_lab.paper_execution.simulation import simulate
from tests.unit.test_paper_models import NOW, instrument, intent, policy, quote


def test_fill_rounds_fee_up_and_is_deterministic() -> None:
    result = simulate(intent(), quote(), policy(), NOW)
    assert result == simulate(intent(), quote(), policy(), NOW)
    assert (result.status, result.quantity, result.notional, result.fee) == (
        "FILLED",
        Decimal("2"),
        Decimal("1"),
        Decimal("0.01"),
    )
    assert result.executed_at_utc == NOW + timedelta(milliseconds=100)
    assert result.consumed == (Decimal("2"),)


def test_explicit_zero_fee() -> None:
    assert simulate(intent(), quote(), policy(fee_bps="0"), NOW).fee == 0


def test_partial_walk_and_depth_reuse() -> None:
    data = quote(
        asks=[
            {"price": "0.50", "quantity": "0.25"},
            {"price": "0.60", "quantity": "0.8"},
            {"price": "0.70", "quantity": "10"},
        ]
    )
    first = simulate(intent(), data, policy(), NOW)
    assert first.status == "PARTIALLY_FILLED"
    assert first.quantity == Decimal("1")
    assert first.notional == Decimal("0.58")
    assert first.fee == Decimal("0.01")
    assert first.consumed == (Decimal("0.2"), Decimal("0.8"), Decimal("0"))
    second = simulate(intent(), data, policy(), NOW, first.consumed)
    assert second.status == "CANCELLED"
    assert second.consumed == first.consumed


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"quality": DataQuality.PARTIAL}, "quote_quality"),
        ({"quality": DataQuality.INVALID}, "quote_quality"),
        ({"event_time_utc": None}, "event_time_unknown"),
        ({"received_at_utc": NOW + timedelta(microseconds=1)}, "quote_unavailable"),
        ({"event_time_utc": NOW + timedelta(seconds=1)}, "quote_in_future"),
        ({"event_time_utc": NOW - timedelta(seconds=60)}, "quote_stale"),
        ({"received_at_utc": NOW - timedelta(seconds=60)}, "quote_stale"),
        ({"instrument": instrument(cash_unit="EUR")}, "instrument_mismatch"),
        ({"asks": [{"price": "0.50", "quantity": "1", "derived": True}]}, "derived_depth"),
        (
            {"asks": [{"price": "0.6", "quantity": "1"}, {"price": "0.5", "quantity": "1"}]},
            "unordered_book",
        ),
        ({"bids": [{"price": "0.7", "quantity": "1"}]}, "invalid_spread"),
        ({"impact_bps": "1"}, "book_impact_not_supported"),
        ({"spot_price": "0.5"}, "ambiguous_quote"),
    ],
)
def test_unexecutable_quotes_fail_closed(changes: dict[str, object], reason: str) -> None:
    result = simulate(intent(), quote(**changes), policy(), NOW)
    assert result.status == "REJECTED"
    assert result.reason == reason
    assert result.quantity == result.notional == result.fee == 0


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"quantity": "2.01"}, "quantity_step"),
        ({"limit_price": "0.601"}, "price_tick"),
        ({"created_at_utc": NOW + timedelta(microseconds=1)}, "intent_in_future"),
        ({"expires_at_utc": NOW + timedelta(milliseconds=100)}, "order_expired"),
    ],
)
def test_intent_admission(changes: dict[str, object], reason: str) -> None:
    result = simulate(intent(**changes), quote(), policy(), NOW)
    assert result.reason == reason
    assert result.status == ("EXPIRED" if reason == "order_expired" else "REJECTED")


def test_naive_clock_fails_closed() -> None:
    assert simulate(intent(), quote(), policy(), NOW.replace(tzinfo=None)).reason == "naive_clock"


def test_cost_and_notional_limits() -> None:
    assert simulate(intent(), quote(), policy(max_cost_bps="20"), NOW).reason == "max_cost_bps"
    assert (
        simulate(intent(), quote(), policy(max_order_notional="1"), NOW).reason
        == "max_order_notional"
    )
    result = simulate(intent(), quote(), policy(max_age_seconds=0), NOW)
    assert result.reason == "quote_stale"


def test_no_liquidity_or_limit_match_cancels() -> None:
    data = quote(asks=[{"price": "0.7", "quantity": "1"}])
    assert simulate(intent(), data, policy(), NOW).status == "CANCELLED"


@pytest.mark.parametrize(
    "used", [(Decimal("11"),), (Decimal("1"), Decimal("1")), (Decimal("-1"),), (Decimal("NaN"),)]
)
def test_invalid_consumption_fails_closed(used: tuple[Decimal, ...]) -> None:
    result = simulate(intent(), quote(), policy(), NOW, used)
    assert result.status == "REJECTED"
    assert result.quantity == 0


@pytest.mark.parametrize(("side", "expected"), [("buy", "0.51"), ("sell", "0.50")])
def test_spot_adverse_impact_and_tick(side: str, expected: str) -> None:
    asset = instrument(kind="spot", source="dexscreener")
    order = intent(
        instrument=asset, side=side, quantity="1", limit_price="0.6" if side == "buy" else "0.4"
    )
    data = quote(
        instrument=asset, bids=[], asks=[], spot_price="0.505", spot_capacity="10", impact_bps="10"
    )
    result = simulate(order, data, policy(), NOW)
    assert result.status == "FILLED"
    assert result.notional == Decimal(expected)
    assert result.fee == Decimal("0.01")
    assert simulate(order, data, policy(), NOW, result.consumed).consumed == (Decimal("2"),)


def test_spot_missing_assumptions_and_impact_cost_gate() -> None:
    asset = instrument(kind="spot")
    order = intent(instrument=asset)
    data = quote(instrument=asset, bids=[], asks=[], spot_price="0.5", spot_capacity="1")
    assert simulate(order, data, policy(), NOW).reason == "spot_assumptions_missing"
    data = quote(
        instrument=asset, bids=[], asks=[], spot_price="0.5", spot_capacity="1", impact_bps="10"
    )
    assert simulate(order, data, policy(max_cost_bps="39"), NOW).reason == "max_cost_bps"
    assert simulate(order, data, policy(max_cost_bps="800"), NOW).status == "PARTIALLY_FILLED"


def test_rounding_per_level_is_adverse_for_cash_and_not_context_dependent() -> None:
    asset = instrument(tick_size="0.001")
    data = quote(
        instrument=asset,
        bids=[{"price": "0.499", "quantity": "10"}],
        asks=[{"price": "0.501", "quantity": "10"}],
    )
    with localcontext() as context:
        context.prec = 2
        bought = simulate(intent(instrument=asset, quantity="0.1"), data, policy(), NOW)
        sold = simulate(
            intent(instrument=asset, side="sell", quantity="0.1", limit_price="0.4"),
            data,
            policy(),
            NOW,
        )
    assert bought.notional == Decimal("0.06")
    assert sold.notional == Decimal("0.04")


def test_event_after_receipt_rejected_even_during_latency() -> None:
    data = quote(event_time_utc=NOW + timedelta(milliseconds=50))
    assert simulate(intent(), data, policy(), NOW).reason == "event_after_receipt"
    assert simulate(intent(), data, policy(future_skew_seconds=1), NOW).status == "FILLED"


def test_fee_rounding_once_across_many_tiny_levels() -> None:
    asset = instrument(quantity_step="1")
    data = quote(
        instrument=asset,
        bids=[{"price": "0.01", "quantity": "100"}],
        asks=[{"price": "0.01", "quantity": "1"}] * 100,
    )
    result = simulate(intent(instrument=asset, quantity="100"), data, policy(), NOW)
    assert result.status == "FILLED"
    assert result.notional == Decimal("1")
    assert result.fee == Decimal("0.01")
    assert result.notional + result.fee == Decimal("1.01")


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_cash_rounding_cannot_breach_effective_limit(side: str) -> None:
    asset = instrument(tick_size="0.001")
    data = quote(
        instrument=asset,
        bids=[{"price": "0.501", "quantity": "1"}],
        asks=[{"price": "0.501", "quantity": "1"}],
    )
    order = intent(instrument=asset, side=side, quantity="0.1", limit_price="0.501")
    result = simulate(order, data, policy(), NOW)
    assert result.status == "CANCELLED"
    assert result.quantity == 0


def test_sell_with_sub_cash_step_notional_never_produces_free_fill() -> None:
    data = quote(
        bids=[{"price": "0.01", "quantity": "1"}], asks=[{"price": "0.01", "quantity": "1"}]
    )
    result = simulate(intent(side="sell", quantity="0.1", limit_price="0.01"), data, policy(), NOW)
    assert result.status == "CANCELLED"
    assert result.quantity == result.fee == 0


def test_simulator_datetime_overflow_fails_closed() -> None:
    from datetime import UTC, datetime

    result = simulate(intent(), quote(), policy(), datetime.max.replace(tzinfo=UTC))
    assert result.status == "REJECTED"
    assert result.reason == "invalid_numeric_or_time_input"


def test_precision_overflow_returns_rejection_with_no_consumption() -> None:
    asset = instrument(kind="spot", tick_size="1", quantity_step="1")
    order = intent(instrument=asset, quantity="1e18", limit_price="1e18")
    data = quote(
        instrument=asset, bids=[], asks=[], spot_price="1e18", spot_capacity="1e18", impact_bps="0"
    )
    assert simulate(order, data, policy(), NOW).reason == "max_order_notional"


def test_exact_freshness_boundary_and_future_skew() -> None:
    data = quote(event_time_utc=NOW - timedelta(seconds=60))
    assert simulate(intent(), data, policy(latency_ms=0), NOW).status == "FILLED"
    future = quote(event_time_utc=NOW + timedelta(seconds=1))
    assert simulate(intent(), future, policy(future_skew_seconds=1), NOW).status == "FILLED"


def test_negative_zero_and_float_consumption() -> None:
    assert simulate(intent(), quote(), policy(), NOW, (Decimal("-0.0"),)).status == "FILLED"


@pytest.mark.parametrize("side", ["buy"])
def test_actual_rounded_fee_enforces_cost_limit_without_consuming_depth(side: str) -> None:
    data = quote(bids=[{"price": "0.5", "quantity": "1"}], asks=[{"price": "0.5", "quantity": "1"}])
    order = intent(side=side, quantity="0.1", limit_price="0.6" if side == "buy" else "0.4")
    for used in ((), (Decimal("0.1"),)):
        result = simulate(order, data, policy(max_cost_bps="100"), NOW, used)
        assert result.status == "REJECTED"
        assert result.reason == "max_cost_bps"
        assert result.quantity == result.notional == result.fee == 0
        assert result.consumed == used
    filled = simulate(order, data, policy(max_cost_bps="4000"), NOW)
    assert filled.status == "FILLED"
    assert filled.notional == Decimal("0.05")
    assert filled.fee == Decimal("0.01")


@pytest.mark.parametrize("side", ["buy"])
def test_depth_weighted_effective_price_enforces_cost_limit(side: str) -> None:
    bids = [{"price": "0.5", "quantity": "100"}]
    asks = [{"price": "0.5", "quantity": "100"}]
    if side == "buy":
        asks = [{"price": "0.5", "quantity": "1"}, {"price": "0.7", "quantity": "9"}]
    else:
        bids = [{"price": "0.5", "quantity": "1"}, {"price": "0.3", "quantity": "9"}]
    data = quote(bids=bids, asks=asks)
    order = intent(side=side, quantity="10", limit_price="0.8" if side == "buy" else "0.2")
    rejected = simulate(order, data, policy(fee_bps="0", max_cost_bps="300"), NOW)
    assert rejected.reason == "max_cost_bps"
    assert rejected.quantity == rejected.notional == rejected.fee == 0
    assert rejected.consumed == ()
    filled = simulate(order, data, policy(fee_bps="0", max_cost_bps="7200"), NOW)
    assert filled.status == "FILLED"
    assert filled.quantity == 10
    assert filled.notional == Decimal("6.8" if side == "buy" else "3.2")


@pytest.mark.parametrize("side", ["buy"])
@pytest.mark.parametrize("rounding", ["tick", "cash"])
def test_cost_limit_includes_adverse_tick_and_cash_rounding(side: str, rounding: str) -> None:
    asset = instrument(kind="spot")
    price = "0.505" if rounding == "tick" else "0.50"
    quantity = "1" if rounding == "tick" else "0.1"
    if rounding == "cash":
        asset = instrument(kind="spot", cash_step="0.03")
    order = intent(
        instrument=asset,
        side=side,
        quantity=quantity,
        limit_price="0.6" if side == "buy" else "0.3",
    )
    data = quote(
        instrument=asset, bids=[], asks=[], spot_price=price, spot_capacity="10", impact_bps="0"
    )
    rejected = simulate(order, data, policy(fee_bps="0", max_cost_bps="10"), NOW)
    assert rejected.reason == "max_cost_bps"
    assert rejected.consumed == ()
    filled = simulate(order, data, policy(fee_bps="0", max_cost_bps="10000"), NOW)
    assert filled.status == "FILLED"
    assert filled.notional == Decimal(
        ("0.51" if side == "buy" else "0.50")
        if rounding == "tick"
        else ("0.06" if side == "buy" else "0.03")
    )


@pytest.mark.parametrize("missing", ["bids", "asks"])
def test_missing_round_trip_cost_reference_fails_closed(missing: str) -> None:
    data = quote(**{missing: []})
    result = simulate(intent(), data, policy(), NOW)
    assert result.status == "REJECTED"
    assert result.reason == "cost_reference_missing"
    assert result.consumed == ()


def test_deep_book_review_regression_never_uses_best_ask_as_fill_cost() -> None:
    data = quote(
        bids=[{"price": "0.49", "quantity": "100"}],
        asks=[{"price": "0.50", "quantity": "1"}, {"price": "1.00", "quantity": "19"}],
    )
    order = intent(quantity="20", limit_price="1")
    result = simulate(order, data, policy(max_cost_bps="300"), NOW)
    assert result.reason == "max_cost_bps"
    assert result.quantity == result.notional == result.fee == 0
    assert result.consumed == ()


def kraken_like(**changes: object) -> PaperInstrument:
    return instrument(
        kind="spot",
        source="kraken",
        venue="kraken",
        native_id="XXBTZEUR",
        base_unit="XBT",
        cash_unit="EUR",
        market_id=None,
        outcome=None,
        tick_size="0.1",
        quantity_step="0.00000001",
        cash_step="0.00001",
        **changes,
    )


def spot_book(**changes: object) -> ExecutionQuote:
    levels: dict[str, object] = {
        "bids": [
            {"price": "66999.9", "quantity": "0.002"},
            {"price": "66990.0", "quantity": "0.050"},
        ],
        "asks": [
            {"price": "67000.1", "quantity": "0.002"},
            {"price": "67010.0", "quantity": "0.050"},
        ],
    }
    return quote(instrument=kraken_like(), **(levels | changes))


def test_spot_instrument_fills_against_a_real_order_book_walking_levels() -> None:
    order = intent(instrument=kraken_like(), quantity="0.003", limit_price="67020.0", side="buy")
    result = simulate(order, spot_book(), policy(fee_bps="80", max_cost_bps="1000"), NOW)
    assert result.status == "FILLED"
    # 0.002 at 67000.1 then 0.001 at 67010.0, each level rounded adversely to the tick.
    assert result.notional == Decimal("0.002") * Decimal("67000.1") + Decimal("0.001") * Decimal(
        "67010.0"
    )
    assert result.fee == (result.notional * Decimal("80") / Decimal(10000)).quantize(
        Decimal("0.00001"), rounding="ROUND_UP"
    )
    assert result.consumed == (Decimal("0.002"), Decimal("0.001"))


def test_spot_sell_walks_bids_best_first_and_respects_the_limit() -> None:
    order = intent(instrument=kraken_like(), quantity="0.003", limit_price="66995.0", side="sell")
    result = simulate(order, spot_book(), policy(fee_bps="80", max_cost_bps="1000"), NOW)
    # The 66990.0 level is below the limit, so only the best level can fill.
    assert result.status == "PARTIALLY_FILLED" and result.quantity == Decimal("0.002")
    assert result.notional == Decimal("0.002") * Decimal("66999.9")


def test_spot_book_with_spot_assumptions_is_ambiguous() -> None:
    data = spot_book().model_copy(
        update={"spot_price": Decimal("67000"), "spot_capacity": Decimal(1)}
    )
    order = intent(instrument=kraken_like(), quantity="0.001", limit_price="67020.0")
    assert simulate(order, data, policy(max_cost_bps="1000"), NOW).reason == "ambiguous_quote"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"bids": [{"price": "67001.0", "quantity": "1"}]}, "invalid_spread"),
        ({"asks": []}, "cost_reference_missing"),
        (
            {
                "asks": [
                    {"price": "67010.0", "quantity": "1"},
                    {"price": "67000.1", "quantity": "1"},
                ]
            },
            "unordered_book",
        ),
    ],
)
def test_spot_book_failures_are_closed(changes: dict[str, object], reason: str) -> None:
    data = spot_book(**changes)
    order = intent(instrument=kraken_like(), quantity="0.001", limit_price="67020.0")
    assert simulate(order, data, policy(max_cost_bps="1000"), NOW).reason == reason


def test_spot_without_a_book_still_uses_the_spot_assumptions_model() -> None:
    asset = kraken_like()
    data = quote(
        instrument=asset, bids=[], asks=[], spot_price="67000.0", spot_capacity="1", impact_bps="10"
    )
    order = intent(instrument=asset, quantity="0.001", limit_price="67100.0")
    assert simulate(order, data, policy(max_cost_bps="1000"), NOW).status == "FILLED"


def test_the_cost_gate_governs_entries_only_a_reducing_sale_is_never_refused_by_it() -> None:
    """A stop-loss must be executable in a stressed, wide book. The gate is an entry-economics
    control; refusing the exit would trap the very position it exists to protect."""
    wide = quote(
        bids=[{"price": "0.30", "quantity": "10"}], asks=[{"price": "0.70", "quantity": "10"}]
    )
    sell = intent(side="sell", quantity="1", limit_price="0.2")
    result = simulate(sell, wide, policy(max_cost_bps="10"), NOW)
    assert result.status == "FILLED" and result.reason == "filled"
    assert result.notional == Decimal("0.30")
    buy = intent(side="buy", quantity="1", limit_price="0.8")
    assert simulate(buy, wide, policy(max_cost_bps="10"), NOW).reason == "max_cost_bps"
    # The same holds on a real spot book and for a spot-assumptions quote.
    book = spot_book(
        bids=[{"price": "60000.0", "quantity": "1"}], asks=[{"price": "70000.0", "quantity": "1"}]
    )
    order = intent(instrument=kraken_like(), side="sell", quantity="0.001", limit_price="50000.0")
    assert simulate(order, book, policy(max_cost_bps="10"), NOW).status == "FILLED"
    asset = kraken_like()
    assumed = quote(
        instrument=asset, bids=[], asks=[], spot_price="0.5", spot_capacity="10", impact_bps="50"
    )
    sale = intent(instrument=asset, side="sell", quantity="0.1", limit_price="0.3")
    assert simulate(sale, assumed, policy(max_cost_bps="10"), NOW).status == "FILLED"
