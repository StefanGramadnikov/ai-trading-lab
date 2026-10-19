"""Bounded synthetic paper scenarios; no market networking or execution endpoints."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictBool, field_validator, model_validator
from sqlalchemy.exc import SQLAlchemyError

from ai_trading_lab.config import Settings
from ai_trading_lab.database import create_database_engine
from ai_trading_lab.market_data.models import DataMode, FrozenModel, parse_json
from ai_trading_lab.paper_execution.models import (
    ExecutionQuote,
    NonnegativeAmount,
    OrderIntent,
    PaperInstrument,
    PositiveAmount,
    SimulationPolicy,
)
from ai_trading_lab.paper_execution.store import PaperStore


class TimedOperation(FrozenModel):
    at: AwareDatetime

    @field_validator("at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class OrderOperation(TimedOperation):
    kind: Literal["order"]
    intent: OrderIntent
    quote: ExecutionQuote


class PauseOperation(TimedOperation):
    kind: Literal["pause"]
    key: str = Field(min_length=1, max_length=200)
    paused: StrictBool


class SettleOperation(TimedOperation):
    kind: Literal["settle"]
    key: str = Field(min_length=1, max_length=200)
    instrument: PaperInstrument
    payout_per_unit: NonnegativeAmount = Field(le=1)
    void: StrictBool


class ReconcileOperation(FrozenModel):
    kind: Literal["reconcile"]


Operation = Annotated[
    OrderOperation | PauseOperation | SettleOperation | ReconcileOperation,
    Field(discriminator="kind"),
]


class Scenario(FrozenModel):
    schema_version: Literal[1]
    data_mode: Literal["FIXTURE"]
    portfolio_id: UUID
    cash_unit: str = Field(min_length=1, max_length=200)
    cash_step: PositiveAmount
    initial_cash: PositiveAmount
    policy: SimulationPolicy
    operations: tuple[Operation, ...] = Field(max_length=100)

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version_requires_integer")
        return value

    @model_validator(mode="after")
    def fixture_scope(self) -> Scenario:
        for operation in self.operations:
            if isinstance(operation, OrderOperation):
                if operation.intent.portfolio_id != self.portfolio_id:
                    raise ValueError("portfolio_mismatch")
                if operation.quote.data_mode != DataMode.FIXTURE:
                    raise ValueError("fixture_quote_required")
        return self


def read_scenario(path: Path) -> Scenario:
    with path.open("rb") as handle:
        content = handle.read(1_000_001)
    if len(content) > 1_000_000:
        raise ValueError("Scenario exceeds 1MB input bound")
    return Scenario.model_validate(parse_json(content.decode("utf-8")))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Offline FIXTURE paper scenarios; PAPER_ONLY")
    commands = result.add_subparsers(dest="command", required=True)
    scenario = commands.add_parser("run", help="Run a bounded synthetic JSON scenario")
    scenario.add_argument("scenario", type=Path)
    return result


async def run(arguments: argparse.Namespace) -> int:
    settings = Settings()
    scenario = await asyncio.to_thread(read_scenario, Path(arguments.scenario))
    engine = create_database_engine(settings)
    try:
        store = PaperStore(engine)
        await store.create(
            scenario.portfolio_id,
            scenario.cash_unit,
            scenario.cash_step,
            scenario.initial_cash,
            scenario.policy,
        )
        counts = {"order": 0, "pause": 0, "settle": 0, "reconcile": 0}
        orders: list[dict[str, str]] = []
        failed = False
        for operation in scenario.operations:
            counts[operation.kind] += 1
            if isinstance(operation, OrderOperation):
                receipt = await store.submit(operation.intent, operation.quote, operation.at)
                orders.append({"status": receipt.result.status, "reason": receipt.result.reason})
                failed |= receipt.result.status in {"REJECTED", "EXPIRED"}
            elif isinstance(operation, PauseOperation):
                await store.pause(
                    scenario.portfolio_id, operation.key, operation.paused, operation.at
                )
            elif isinstance(operation, SettleOperation):
                await store.settle(
                    scenario.portfolio_id,
                    operation.key,
                    operation.instrument,
                    operation.payout_per_unit,
                    operation.void,
                    operation.at,
                )
            else:
                failed |= not await store.reconcile(scenario.portfolio_id)
        reconciled = await store.reconcile(scenario.portfolio_id)
        final_state = await store.get(scenario.portfolio_id)
    finally:
        await engine.dispose()
    print(
        json.dumps(
            {
                "mode": "FIXTURE",
                "portfolio_id": str(scenario.portfolio_id),
                "operation_counts": counts,
                "orders": orders,
                "final_state": final_state.model_dump(mode="json"),
                "reconciled": reconciled,
            }
        )
    )
    return 1 if failed or not reconciled else 0


def main() -> None:
    arguments = parser().parse_args()
    try:
        result = asyncio.run(run(arguments))
    except (SQLAlchemyError, OSError, ValueError):
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": "Invalid scenario, configuration or persistence failure",
                }
            )
        )
        result = 2
    raise SystemExit(result)


if __name__ == "__main__":
    main()
