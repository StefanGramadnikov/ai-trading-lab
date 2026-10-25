"""Oversized local evidence is an abstention with complete-input provenance."""

import hashlib
import json
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from ai_trading_lab.ai.evidence import build_evidence
from ai_trading_lab.ai.orchestration import orchestrate
from ai_trading_lab.market_data.storage import content_hash
from ai_trading_lab.paper_execution.accounting import open_portfolio
from ai_trading_lab.strategies.models import PricePoint
from tests.ai_helpers import ai_spec


@pytest.mark.asyncio
async def test_f16_oversized_evidence_abstains_without_call_and_next_frame_continues() -> None:
    spec = ai_spec()
    frame = spec.research.frames[0]
    point = PricePoint(
        instrument_key=frame.quote.instrument.key,
        data_mode="FIXTURE",
        price="0.5",
        event_time_utc=frame.at_utc,
        available_at_utc=frame.at_utc,
    )
    large = frame.model_copy(update={"history": (point,) * 100})
    state, _ = open_portfolio(uuid4(), "USD", Decimal("0.01"), Decimal(100))
    packet = build_evidence(large, state)
    assert packet == build_evidence(large, state)
    assert len(packet.payload) <= 16000
    payload = json.loads(packet.payload)
    assert payload["failure_reason"] == "evidence_too_large"
    assert payload["omitted_sections"] == ["frame", "portfolio"]
    original = json.dumps(
        {
            "frame": large.model_dump(mode="json"),
            "portfolio": state.model_dump(mode="json"),
            "label": "untrusted_data_only",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    assert payload["full_payload_sha256"] == hashlib.sha256(original.encode()).hexdigest()
    assert payload["full_payload_bytes"] == len(original.encode())
    assert packet.evidence_ids == (
        str(frame.quote.snapshot_id),
        content_hash(large.model_dump(mode="json")),
    )
    invoke = AsyncMock(return_value=spec.fixture_results[0])
    result = await orchestrate(spec.flow, packet, invoke)
    assert result.final.action == "abstain"
    assert result.reason == "evidence_too_large"
    assert result.final.evidence_ids == packet.evidence_ids
    assert result.trace == ()
    invoke.assert_not_awaited()
    # The same caller can proceed with the next bounded frame without consuming
    # a provider result or poisoning its inference budget on the oversized one.
    result = await orchestrate(spec.flow, build_evidence(frame, state), invoke)
    assert result.reason == "completed"
    invoke.assert_awaited_once()
