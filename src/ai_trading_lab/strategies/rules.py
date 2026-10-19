"""Deterministic hypotheses with no order-sizing or execution authority."""

from datetime import datetime, timedelta
from decimal import localcontext

from ai_trading_lab.market_data.models import DataMode, DataQuality
from ai_trading_lab.paper_execution.accounting import PortfolioState
from ai_trading_lab.strategies.models import (
    Abstain,
    CashConfig,
    MomentumConfig,
    ProbabilityConfig,
    StrategyConfig,
    StrategyFrame,
    TradeProposal,
)


def _available(event: datetime, receipt: datetime, now: datetime, max_age: int) -> bool:
    return event <= receipt <= now and now - event <= timedelta(seconds=max_age)


def _momentum(config: MomentumConfig, frame: StrategyFrame, held: bool) -> TradeProposal | Abstain:
    quote, feature = frame.quote, frame.momentum
    if quote.instrument.kind != "spot" or quote.spot_price is None:
        return Abstain(reason="spot_price_required")
    if feature is None:
        return Abstain(reason="momentum_features_required")
    if (
        feature.instrument_key != quote.instrument.key
        or feature.data_mode != quote.data_mode
        or not _available(
            feature.event_time_utc,
            feature.available_at_utc,
            frame.at_utc,
            config.max_age_seconds,
        )
    ):
        return Abstain(reason="momentum_evidence_unqualified")
    if feature.volume_6h is None or feature.volume_24h is None:
        return Abstain(reason="volume_required")
    ratio = feature.volume_6h * 4 / feature.volume_24h
    evidence = (str(quote.snapshot_id), f"feature:{feature.event_time_utc.isoformat()}")
    if held:
        if ratio < config.exit_volume_ratio:
            return TradeProposal(
                side="sell",
                limit_price=quote.spot_price,
                reason="volume_exhaustion",
                evidence=evidence,
            )
        return Abstain(reason="holding_without_exit", evidence=evidence)
    if (
        feature.pool_created_at_utc is None
        or feature.pool_created_at_utc > feature.event_time_utc
        or frame.at_utc - feature.pool_created_at_utc
        > timedelta(seconds=config.max_pool_age_seconds)
        or feature.liquidity is None
        or feature.liquidity_unit != quote.instrument.cash_unit
        or feature.liquidity < config.min_liquidity
    ):
        return Abstain(reason="pool_liquidity_unqualified", evidence=evidence)
    # Token accounts are not unique owners. An optional ownership screen requires
    # an explicitly supplied, indexed ownership result rather than an RPC guess.
    if config.max_owner_share is not None and (
        feature.verified_owner_share is None
        or feature.ownership_basis != "unique_owner_balances"
        or feature.verified_owner_share > config.max_owner_share
    ):
        return Abstain(reason="ownership_unqualified", evidence=evidence)
    cutoff = frame.at_utc - timedelta(seconds=config.lookback_seconds)
    if not frame.history or any(
        point.instrument_key != quote.instrument.key
        or point.data_mode != quote.data_mode
        or not point.event_time_utc <= point.available_at_utc <= frame.at_utc
        for point in frame.history
    ):
        return Abstain(reason="price_history_unqualified", evidence=evidence)
    # The nearest sample at/before the lookback must be no more than max_age old
    # relative to that boundary, rather than relative to the current quote.
    candidates = [
        point
        for point in frame.history
        if point.event_time_utc <= cutoff
        and cutoff - point.event_time_utc <= timedelta(seconds=config.max_age_seconds)
    ]
    if not candidates:
        return Abstain(reason="lookback_history_required", evidence=evidence)
    by_time = {point.event_time_utc: point for point in frame.history}
    if any(by_time[point.event_time_utc] != point for point in frame.history):
        return Abstain(reason="conflicting_price_history", evidence=evidence)
    previous = max(candidates, key=lambda point: point.event_time_utc)
    momentum = (quote.spot_price / previous.price - 1) * 10000
    if momentum < config.min_momentum_bps or ratio < config.min_volume_ratio:
        return Abstain(reason="entry_threshold_not_met", evidence=evidence)
    return TradeProposal(
        side="buy",
        limit_price=quote.spot_price,
        reason="pool_momentum",
        evidence=(*evidence, previous.event_time_utc.isoformat()),
    )


def _probability(
    config: ProbabilityConfig, frame: StrategyFrame, held: bool
) -> TradeProposal | Abstain:
    quote, forecast = frame.quote, frame.forecast
    instrument = quote.instrument
    if instrument.kind != "prediction" or not quote.bids or not quote.asks:
        return Abstain(reason="prediction_book_required")
    if (
        any(level.derived for level in (*quote.bids, *quote.asks))
        or list(quote.bids) != sorted(quote.bids, key=lambda level: level.price, reverse=True)
        or list(quote.asks) != sorted(quote.asks, key=lambda level: level.price)
        or quote.bids[0].price > quote.asks[0].price
    ):
        return Abstain(reason="prediction_book_unqualified")
    if forecast is None:
        return Abstain(reason="forecast_required")
    if (
        forecast.instrument_key != instrument.key
        or frame.resolution_terms_hash is None
        or forecast.resolution_terms_hash != frame.resolution_terms_hash
        or forecast.market_id != instrument.market_id
        or forecast.outcome != instrument.outcome
        or forecast.data_mode != quote.data_mode
        or not forecast.independent
        or not _available(
            forecast.produced_at_utc,
            forecast.available_at_utc,
            frame.at_utc,
            config.max_age_seconds,
        )
    ):
        return Abstain(reason="forecast_unqualified")
    evidence = (
        str(quote.snapshot_id),
        forecast.provenance,
        forecast.version,
        forecast.calibration_reference,
    )
    price = quote.bids[0].price if held else quote.asks[0].price
    edge = price - forecast.probability if held else forecast.probability - price
    if price <= 0 or edge - config.cost_buffer < config.min_edge:
        return Abstain(reason="net_edge_not_met", evidence=evidence)
    return TradeProposal(
        side="sell" if held else "buy",
        limit_price=price,
        reason="independent_probability_edge",
        evidence=evidence,
    )


def decide(
    config: StrategyConfig, frame: StrategyFrame, state: PortfolioState
) -> TradeProposal | Abstain:
    """Evaluate only information available by the frame's virtual decision clock."""
    with localcontext() as context:
        context.prec = 100
        if isinstance(config, CashConfig):
            return Abstain(reason="cash_benchmark")
        quote = frame.quote
        if (
            quote.quality != DataQuality.VALID
            or quote.data_mode == DataMode.OBSERVED
            or quote.event_time_utc is None
            or not _available(
                quote.event_time_utc,
                quote.received_at_utc,
                frame.at_utc,
                config.max_age_seconds,
            )
        ):
            return Abstain(reason="quote_unqualified")
        if (state.cash_unit, state.cash_step) != (
            quote.instrument.cash_unit,
            quote.instrument.cash_step,
        ):
            return Abstain(reason="portfolio_unit_mismatch")
        held = any(position.instrument == quote.instrument for position in state.positions)
        if isinstance(config, MomentumConfig):
            return _momentum(config, frame, held)
        return _probability(config, frame, held)
