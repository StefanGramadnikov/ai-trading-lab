"""Entirely synthetic research tapes; parameters have no operational meaning."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from ai_trading_lab.backtesting.models import ResearchInput
from ai_trading_lab.strategies.models import Forecast, ProbabilityConfig, StrategyFrame
from tests.integration.test_paper_store import NOW, asset, policy, quote

TERMS = "a" * 64


def research() -> ResearchInput:
    instrument = asset(True)
    snapshot = quote(instrument, "0.50", "100")
    forecast = Forecast(
        instrument_key=instrument.key,
        market_id=instrument.market_id,
        outcome=instrument.outcome,
        probability=Decimal("0.8"),
        provenance="synthetic-test-only",
        version="test-v1",
        calibration_reference="not-calibrated",
        resolution_terms_hash=TERMS,
        independent=True,
        data_mode="FIXTURE",
        produced_at_utc=NOW,
        available_at_utc=NOW,
    )
    frame = StrategyFrame(
        quote=snapshot, at_utc=NOW, forecast=forecast, resolution_terms_hash=TERMS
    )
    return ResearchInput(
        run_id=uuid4(),
        code_version="0" * 40,
        strategy=ProbabilityConfig(
            version="synthetic-v1",
            max_age_seconds=60,
            min_edge=Decimal("0.1"),
            cost_buffer=Decimal("0.01"),
        ),
        simulation=policy(),
        cash_unit="USD",
        cash_step=Decimal("0.01"),
        initial_cash=Decimal(100),
        order_notional=Decimal(10),
        order_ttl_seconds=30,
        start_at_utc=NOW,
        end_at_utc=NOW + timedelta(seconds=1),
        data_mode="FIXTURE",
        frames=(frame,),
    )
