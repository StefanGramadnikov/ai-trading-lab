"""Build a bounded data-only packet from already qualified local evidence."""

from ai_trading_lab.ai.models import Evidence
from ai_trading_lab.market_data.models import DataQuality
from ai_trading_lab.market_data.storage import content_hash
from ai_trading_lab.paper_execution.accounting import PortfolioState
from ai_trading_lab.strategies.models import StrategyFrame


def build_evidence(frame: StrategyFrame, state: PortfolioState) -> Evidence:
    quote = frame.quote
    if (
        quote.quality != DataQuality.VALID
        or quote.event_time_utc is None
        or not quote.event_time_utc <= quote.received_at_utc <= frame.at_utc
    ):
        raise ValueError("unavailable_market_evidence")
    if frame.forecast is not None and not (
        frame.forecast.produced_at_utc <= frame.forecast.available_at_utc <= frame.at_utc
    ):
        raise ValueError("unavailable_forecast_evidence")
    if frame.momentum is not None and not (
        frame.momentum.event_time_utc <= frame.momentum.available_at_utc <= frame.at_utc
    ):
        raise ValueError("unavailable_momentum_evidence")
    if any(not p.event_time_utc <= p.available_at_utc <= frame.at_utc for p in frame.history):
        raise ValueError("unavailable_history_evidence")
    # Full frame identity and immutable content hashes constrain cited evidence.
    ids = (str(quote.snapshot_id), content_hash(frame.model_dump(mode="json")))
    import json

    payload = json.dumps(
        {
            "frame": frame.model_dump(mode="json"),
            "portfolio": state.model_dump(mode="json"),
            "label": "untrusted_data_only",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return Evidence(
        instrument_key=quote.instrument.key,
        snapshot_id=quote.snapshot_id,
        available_at_utc=frame.at_utc,
        evidence_ids=ids,
        payload=payload,
    )
