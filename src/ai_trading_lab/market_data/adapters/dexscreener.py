"""Bounded Solana pair sampling; profiles are not exhaustive token discovery."""

from collections.abc import Sequence
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

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
Identity = Annotated[str, Field(strict=True, min_length=1, max_length=128)]
Amount = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


class _DTO(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class _Profile(_DTO):
    chainId: StrictStr
    tokenAddress: Mint


class _Token(_DTO):
    address: Mint


class _Liquidity(_DTO):
    usd: Amount | None = None


class _Pair(_DTO):
    chainId: Literal["solana"]
    dexId: Identity
    pairAddress: Mint
    baseToken: _Token
    quoteToken: _Token
    priceUsd: Amount | None = None
    liquidity: _Liquidity | None = None


class _Seed(_DTO):
    mint: Mint


class DexScreenerAdapter:
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
                    source=Source.DEXSCREENER,
                    target=target,
                    code=code,
                    message="DexScreener response unavailable or invalid",
                )
            )

        async def fetch(path: str) -> object:
            capture = await self.transport.request(Source.DEXSCREENER, "GET", path)
            captures.append(capture)
            if (
                capture.source != Source.DEXSCREENER
                or capture.path != path
                or capture.method != "GET"
                or capture.params
                or capture.request_body is not None
            ):
                raise ValueError("capture identity mismatch")
            if capture.status_code != 200:
                raise TransportError(Source.DEXSCREENER, "http_error")
            payload = capture.payload()
            if not isinstance(payload, list):
                raise ValueError("expected an array")
            return payload

        seeds = list(dict.fromkeys(mint_addresses[:limit]))
        if not mint_addresses:
            path = "/token-profiles/latest/v1"
            try:
                profiles = await fetch(path)
                assert isinstance(profiles, list)
                for raw in profiles:
                    try:
                        profile = _Profile.model_validate(raw)
                        if profile.chainId == "solana" and profile.tokenAddress not in seeds:
                            seeds.append(profile.tokenAddress)
                            if len(seeds) >= limit:
                                break
                    except ValidationError:
                        fail(path, "invalid_profile")
            except (TransportError, ValueError) as exc:
                if isinstance(exc, TransportError) and exc.capture is not None:
                    captures.append(exc.capture)
                fail(path, exc.code if isinstance(exc, TransportError) else "invalid_payload")
        seen_pairs: set[str] = set()
        for mint in seeds:
            if len(observations) >= limit:
                break
            try:
                _Seed(mint=mint)
            except ValidationError:
                fail("mint", "invalid_identity")
                continue
            path = f"/token-pairs/v1/solana/{mint}"
            try:
                pairs = await fetch(path)
                assert isinstance(pairs, list)
                capture = captures[-1]
                for raw in pairs:
                    if len(observations) >= limit:
                        break
                    try:
                        pair = _Pair.model_validate(raw)
                        # USD price describes the base token, even for a quote-token query.
                        if mint not in (pair.baseToken.address, pair.quoteToken.address):
                            raise ValueError("requested token absent from pair")
                        if pair.pairAddress in seen_pairs:
                            continue
                        seen_pairs.add(pair.pairAddress)
                        issues = ["source_time_unknown"]
                        if pair.priceUsd is None:
                            issues.append("price_missing")
                        liquidity = pair.liquidity.usd if pair.liquidity else None
                        if liquidity is None:
                            issues.append("liquidity_missing")
                        observations.append(
                            Observation(
                                capture_id=capture.capture_id,
                                instrument=Instrument(
                                    source=Source.DEXSCREENER,
                                    venue=pair.dexId,
                                    native_id=pair.pairAddress,
                                    kind="spot",
                                    base_asset=pair.baseToken.address,
                                    quote_currency=pair.quoteToken.address,
                                ),
                                received_at_utc=capture.received_at_utc,
                                issues=tuple(issues),
                                price=pair.priceUsd,
                                price_currency="USD" if pair.priceUsd is not None else None,
                                liquidity=liquidity,
                                liquidity_currency="USD" if liquidity is not None else None,
                                facts={
                                    "data_kind": "pair_snapshot",
                                    "requested_mint": mint,
                                    "price_basis": "USD per base token",
                                    "discovery_coverage": "bounded sample; not exhaustive",
                                },
                            )
                        )
                    except (ValidationError, ValueError):
                        fail(path, "invalid_pair")
            except (TransportError, ValueError) as exc:
                if isinstance(exc, TransportError) and exc.capture is not None:
                    captures.append(exc.capture)
                fail(path, exc.code if isinstance(exc, TransportError) else "invalid_payload")
        return Batch(
            captures=tuple(captures), observations=tuple(observations), failures=tuple(failures)
        )
