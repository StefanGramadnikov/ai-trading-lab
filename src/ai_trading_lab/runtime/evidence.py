"""Original observed captures retain their provenance and native depth identity."""

from uuid import NAMESPACE_URL, uuid5

from ai_trading_lab.market_data.models import DataMode
from ai_trading_lab.paper_execution.models import ExecutionQuote
from ai_trading_lab.paper_execution.replay import ReplayEntry


def native_event_id(quote: ExecutionQuote) -> str:
    instrument = quote.instrument
    return str(
        uuid5(
            NAMESPACE_URL,
            "|".join(
                (
                    "M8-native-v1",
                    instrument.source.value,
                    instrument.venue,
                    instrument.native_id,
                    str(quote.event_time_utc),
                )
            ),
        )
    )


def observed_quote(entry: ReplayEntry) -> ExecutionQuote:
    entry = ReplayEntry.model_validate(entry.model_dump(mode="json"))
    quote = entry.derive_quote()
    return ExecutionQuote.model_validate(
        {
            **quote.model_dump(mode="json"),
            "snapshot_id": native_event_id(quote),
            "data_mode": DataMode.OBSERVED,
        }
    )
