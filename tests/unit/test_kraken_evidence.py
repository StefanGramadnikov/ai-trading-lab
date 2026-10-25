"""Kraken orders are only admitted when their quote re-derives from the raw captures."""

import json
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from ai_trading_lab.market_data.adapters.kraken import KrakenAdapter
from ai_trading_lab.market_data.models import Capture, DataMode, DataQuality, Source
from ai_trading_lab.paper_execution.history import History
from ai_trading_lab.paper_execution.kraken_evidence import KrakenEntry
from ai_trading_lab.paper_execution.models import OrderIntent, SimulationPolicy
from tests.contract.test_kraken import RECEIVED, SERVER, Synthetic

PORTFOLIO = UUID(int=7)


async def captures(mode: DataMode = DataMode.OBSERVED) -> dict[str, Capture]:
    batch = await KrakenAdapter(Synthetic()).collect(("XBTUSD",))
    return {
        c.path.rsplit("/", 1)[-1]: c.model_copy(update={"data_mode": mode}) for c in batch.captures
    }


async def entry(mode: DataMode = DataMode.OBSERVED, **changes: Any) -> KrakenEntry:
    got = await captures(mode)
    parts: dict[str, Any] = {
        "pairs_capture": got["AssetPairs"],
        "time_capture": got["Time"],
        "book_capture": got["Depth"],
        "pair": "XBTUSD",
        "assumption_version": "v1",
    }
    return KrakenEntry.model_validate(parts | changes)


def rewrite(capture: Capture, edit: Any) -> Capture:
    body = json.loads(capture.body)
    edit(body)
    return capture.model_copy(update={"body": json.dumps(body)})


@pytest.mark.asyncio
async def test_quote_is_derived_from_raw_captures_with_the_venues_own_rules() -> None:
    quote = (await entry()).quote
    instrument = quote.instrument
    assert (instrument.source, instrument.venue, instrument.kind) == (
        Source.KRAKEN,
        "kraken",
        "spot",
    )
    assert (instrument.base_unit, instrument.cash_unit) == ("XBT", "USD")
    assert instrument.tick_size == Decimal("0.1")
    assert instrument.quantity_step == Decimal("0.00000001")
    assert instrument.cash_step == Decimal("0.00001")
    assert quote.data_mode == DataMode.OBSERVED and quote.quality == DataQuality.VALID
    assert quote.event_time_utc is not None and quote.event_time_utc.timestamp() == SERVER
    assert quote.received_at_utc == RECEIVED
    assert [lv.price for lv in quote.bids] == [Decimal("66999.9"), Decimal("66999.5")]
    assert [lv.price for lv in quote.asks] == [Decimal("67000.1"), Decimal("67000.5")]


@pytest.mark.asyncio
async def test_derivation_is_deterministic_for_the_same_captures() -> None:
    item = await entry()
    again = KrakenEntry.model_validate_json(item.model_dump_json())
    assert item.quote == again.quote == item.quote
    assert item.quote.snapshot_id == again.quote.snapshot_id


@pytest.mark.asyncio
async def test_mixed_data_modes_are_rejected() -> None:
    got = await captures()
    with pytest.raises(ValueError, match="mode"):
        await entry(time_capture=got["Time"].model_copy(update={"data_mode": DataMode.FIXTURE}))


@pytest.mark.asyncio
async def test_a_book_stamped_after_its_server_time_is_rejected() -> None:
    got = await captures()
    late = rewrite(got["Time"], lambda body: body["result"].__setitem__("unixtime", SERVER - 30))
    with pytest.raises(ValueError, match="newer"):
        await entry(time_capture=late)


@pytest.mark.asyncio
async def test_a_server_time_that_lags_the_book_makes_the_quote_partial_not_valid() -> None:
    got = await captures()

    def older(body: dict[str, Any]) -> None:
        for side in ("asks", "bids"):
            for level in body["result"]["XXBTZUSD"][side]:
                level[2] = SERVER - 40

    lagging = await entry(
        time_capture=rewrite(
            got["Time"], lambda b: b["result"].__setitem__("unixtime", SERVER - 30)
        ),
        book_capture=rewrite(got["Depth"], older),
    )
    assert lagging.quote.quality == DataQuality.PARTIAL


@pytest.mark.asyncio
async def test_unknown_pair_offline_pair_and_wrong_capture_paths_are_rejected() -> None:
    got = await captures()
    with pytest.raises(ValueError):
        await entry(pair="ETHUSD")
    offline = rewrite(
        got["AssetPairs"], lambda b: b["result"]["XXBTZUSD"].__setitem__("status", "cancel_only")
    )
    with pytest.raises(ValueError):
        await entry(pairs_capture=offline)
    with pytest.raises(ValueError):
        await entry(time_capture=got["Depth"])
    with pytest.raises(ValueError):
        await entry(book_capture=got["Time"])


@pytest.mark.asyncio
async def test_unordered_crossed_or_overlong_books_are_rejected_not_repaired() -> None:
    got = await captures()
    with pytest.raises(ValueError):
        await entry(
            book_capture=rewrite(got["Depth"], lambda b: b["result"]["XXBTZUSD"]["asks"].reverse())
        )
    with pytest.raises(ValueError):
        await entry(
            book_capture=rewrite(
                got["Depth"],
                lambda b: b["result"]["XXBTZUSD"]["bids"][0].__setitem__(0, "67001.0"),
            )
        )


def live_config(**extra: object) -> dict[str, object]:
    return {
        "cash_unit": "USD",
        "cash_step": "0.00001",
        "initial_cash": "100",
        "policy": SimulationPolicy.model_validate(
            {
                "version": "v1",
                "fee_bps": "80",
                "latency_ms": 1000,
                "max_age_seconds": 60,
                "future_skew_seconds": 5,
                "max_order_notional": "25",
                "max_position_notional": "25",
                "max_total_exposure": "50",
                "max_cost_bps": "250",
            }
        ).model_dump(mode="json"),
        "live_session_id": str(uuid4()),
        "live_data_mode": "OBSERVED",
        **extra,
    }


async def buy_payload(item: KrakenEntry, quantity: str = "0.0002") -> dict[str, object]:
    quote = item.quote
    intent = OrderIntent.model_validate(
        {
            "portfolio_id": PORTFOLIO,
            "idempotency_key": "k1",
            "instrument": quote.instrument,
            "side": "buy",
            "quantity": quantity,
            "limit_price": "67050.0",
            "created_at_utc": RECEIVED,
            "expires_at_utc": RECEIVED.replace(minute=5),
        }
    )
    return {
        "intent": intent.model_dump(mode="json"),
        "quote": quote.model_dump(mode="json"),
        "source_entry": item.model_dump(mode="json"),
    }


@pytest.mark.asyncio
async def test_history_fills_an_observed_quote_only_with_matching_raw_evidence() -> None:
    history = History(PORTFOLIO, live_config())
    effect = history.apply("order", "k1", await buy_payload(await entry()), RECEIVED)
    assert effect.receipt is not None and effect.receipt.result.status == "FILLED"
    assert effect.receipt.result.fee > 0


@pytest.mark.asyncio
async def test_history_rejects_a_forged_quote_missing_evidence_and_unmarked_portfolios() -> None:
    good = await buy_payload(await entry())
    forged = json.loads(json.dumps(good))
    forged["quote"]["asks"][0]["price"] = "60000.0"  # cheaper than the captured book
    with pytest.raises(ValueError, match="mismatch"):
        History(PORTFOLIO, live_config()).apply("order", "k1", forged, RECEIVED)
    missing = {k: v for k, v in good.items() if k != "source_entry"}
    with pytest.raises(ValueError, match="evidence"):
        History(PORTFOLIO, live_config()).apply("order", "k1", missing, RECEIVED)
    # A portfolio that is not a live-observed one still refuses OBSERVED quotes.
    plain = {k: v for k, v in live_config().items() if not k.startswith("live_")}
    effect = History(PORTFOLIO, plain).apply("order", "k1", good, RECEIVED)
    assert effect.receipt is not None and effect.receipt.result.reason == "fixture_mode_required"
