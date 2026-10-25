"""Build a bounded data-only packet from already qualified local evidence."""

import hashlib
import json

from ai_trading_lab.ai.models import Evidence
from ai_trading_lab.market_data.models import DataQuality
from ai_trading_lab.market_data.storage import content_hash
from ai_trading_lab.paper_execution.accounting import PortfolioState
from ai_trading_lab.strategies.models import StrategyFrame

MAX_PAYLOAD_CHARS = 16000
TOO_LARGE = "evidence_too_large"
# Canonical keys are sorted, so this reason is always the packet's first member.
_TOO_LARGE_PREFIX = '{"failure_reason":"' + TOO_LARGE + '"'


def evidence_failure(evidence: Evidence) -> str | None:
    """Reason a locally built packet must not be sent to any provider, if any."""
    return TOO_LARGE if evidence.payload.startswith(_TOO_LARGE_PREFIX) else None


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
    payload = json.dumps(
        {
            "frame": frame.model_dump(mode="json"),
            "portfolio": state.model_dump(mode="json"),
            "label": "untrusted_data_only",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(payload) > MAX_PAYLOAD_CHARS:
        # Never truncate silently or abort the run: record the complete-input digest and
        # let the caller abstain without transmitting an incomplete packet to a provider.
        encoded = payload.encode()
        payload = json.dumps(
            {
                "failure_reason": TOO_LARGE,
                "full_payload_bytes": len(encoded),
                "full_payload_sha256": hashlib.sha256(encoded).hexdigest(),
                "label": "untrusted_data_only",
                "omitted_sections": ["frame", "portfolio"],
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
