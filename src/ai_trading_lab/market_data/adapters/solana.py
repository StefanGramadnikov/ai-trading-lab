"""Read-only finalized token metrics; token accounts are not unique holders."""

from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from ai_trading_lab.market_data.models import (
    Batch,
    Capture,
    Failure,
    Instrument,
    Observation,
    Source,
    Transport,
    TransportError,
)

Mint = Annotated[str, Field(strict=True, pattern=r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")]
RawAmount = Annotated[str, Field(strict=True, pattern=r"^[0-9]{1,20}$")]
NonnegativeInt = Annotated[int, Field(strict=True, ge=0)]


class _DTO(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class _Seed(_DTO):
    mint: Mint


class _Context(_DTO):
    slot: NonnegativeInt


class _Amount(_DTO):
    amount: RawAmount
    decimals: Annotated[int, Field(strict=True, ge=0, le=255)]


class _Account(_Amount):
    address: Mint


class _Supply(_DTO):
    context: _Context
    value: _Amount


class _Largest(_DTO):
    context: _Context
    value: Annotated[list[_Account], Field(max_length=20)]


class _Envelope(_DTO):
    jsonrpc: Literal["2.0"]
    id: str
    result: object = None
    error: object = None


class SolanaAdapter:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    async def collect(self, mint_addresses: Sequence[str] = (), *, limit: int = 5) -> Batch:
        if isinstance(limit, bool) or not 1 <= limit <= 10:
            raise ValueError("limit must be between 1 and 10")
        captures: list[Capture] = []
        observations: list[Observation] = []
        failures: list[Failure] = []

        def fail(target: str, code: str) -> None:
            failures.append(
                Failure(
                    source=Source.SOLANA,
                    target=target,
                    code=code,
                    message="Solana response unavailable or invalid",
                )
            )

        if not mint_addresses:
            fail("mints", "explicit_mints_required")
        for mint in dict.fromkeys(mint_addresses[:limit]):
            try:
                _Seed(mint=mint)
            except ValidationError:
                fail("mint", "invalid_identity")
                continue
            instrument = Instrument(
                source=Source.SOLANA, venue="solana", native_id=mint, base_asset=mint, kind="token"
            )
            for method in ("getTokenSupply", "getTokenLargestAccounts"):
                body: dict[str, JsonValue] = {
                    "jsonrpc": "2.0",
                    "id": method,
                    "method": method,
                    "params": [mint, {"commitment": "finalized"}],
                }
                try:
                    capture = await self.transport.request(
                        Source.SOLANA, "POST", "/", json_body=body
                    )
                    captures.append(capture)
                    if (
                        capture.source != Source.SOLANA
                        or capture.method != "POST"
                        or capture.params
                        or capture.path != "/"
                        or capture.request_body != body
                    ):
                        raise ValueError("capture request identity mismatch")
                    if capture.status_code != 200:
                        raise TransportError(Source.SOLANA, "http_error")
                    envelope = _Envelope.model_validate(capture.payload())
                    if envelope.id != method:
                        raise ValueError("RPC response id mismatch")
                    if envelope.error is not None:
                        fail(f"{mint}:{method}", "rpc_error")
                        continue
                    facts: dict[str, JsonValue] = {"data_kind": method, "commitment": "finalized"}
                    if method == "getTokenSupply":
                        supply = _Supply.model_validate(envelope.result)
                        if int(supply.value.amount) > 2**64 - 1:
                            raise ValueError("amount exceeds u64")
                        facts.update(
                            {
                                "raw_amount": supply.value.amount,
                                "decimals": supply.value.decimals,
                                "context_slot": supply.context.slot,
                                "unit": "raw token units",
                            }
                        )
                    else:
                        largest = _Largest.model_validate(envelope.result)
                        if len({a.address for a in largest.value}) != len(largest.value):
                            raise ValueError("duplicate token accounts")
                        if len({a.decimals for a in largest.value}) > 1:
                            raise ValueError("inconsistent token decimals")
                        if any(int(a.amount) > 2**64 - 1 for a in largest.value):
                            raise ValueError("amount exceeds u64")
                        accounts: list[JsonValue] = [
                            {"address": a.address, "raw_amount": a.amount, "decimals": a.decimals}
                            for a in largest.value
                        ]
                        facts.update(
                            {
                                "context_slot": largest.context.slot,
                                "token_accounts": accounts,
                                "coverage": "up to 20 largest token accounts; not holders",
                                "unit": "raw token units",
                            }
                        )
                    observations.append(
                        Observation(
                            capture_id=capture.capture_id,
                            instrument=instrument,
                            received_at_utc=capture.received_at_utc,
                            issues=("source_time_unknown",),
                            facts=facts,
                        )
                    )
                except (TransportError, ValueError) as exc:
                    if isinstance(exc, TransportError) and exc.capture is not None:
                        captures.append(exc.capture)
                    fail(
                        f"{mint}:{method}",
                        exc.code if isinstance(exc, TransportError) else "invalid_payload",
                    )
        return Batch(
            captures=tuple(captures), observations=tuple(observations), failures=tuple(failures)
        )
