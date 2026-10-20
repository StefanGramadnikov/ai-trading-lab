"""Matched paper comparisons and once-only expense attribution; never infer missing costs."""

from decimal import Decimal, localcontext
from typing import cast

from ai_trading_lab.backtesting.models import runtime_fingerprint
from ai_trading_lab.experiments.costs import Expense, allocate_costs
from ai_trading_lab.experiments.models import ExperimentConfig


def build_report(
    config: ExperimentConfig, reports: tuple[dict[str, object], ...]
) -> dict[str, object]:
    config = ExperimentConfig.model_validate(config.model_dump(mode="json"))
    by_id: dict[str, dict[str, object]] = {}
    expenses = list(config.expenses)
    for report in reports:
        cell_id = report.get("cell_id")
        if not isinstance(cell_id, str) or cell_id in by_id:
            raise ValueError("duplicate or missing cell result")
        cell = next((c for c in config.cells if c.cell_id == cell_id), None)
        if cell is None:
            raise ValueError("unknown cell result")
        research = cell.profile.effective(cell.research)
        if (
            report.get("input_hash") != research.fingerprint
            or report.get("dataset_hash") != research.dataset_hash
            or report.get("engine_hash") != runtime_fingerprint()
        ):
            raise ValueError("experiment cell input/runtime mismatch")
        raw = report.get("inference_expenses")
        if not isinstance(raw, list):
            raise ValueError("missing inference expense evidence")
        expenses.extend(Expense.model_validate(e) for e in raw)
        by_id[cell_id] = report
    costs = allocate_costs(
        expenses, tuple(c.cell_id for c in config.cells), config.reporting_currency, config.fx_rates
    )
    cells: list[dict[str, object]] = []
    sufficient = len(reports) == len(config.cells)
    comparable: dict[str, Decimal | None] = {}
    trading: dict[str, Decimal | None] = {}
    with localcontext() as ctx:
        ctx.prec = 160
        for cell, allocated in zip(config.cells, costs.per_cell, strict=True):
            result = by_id.get(cell.cell_id)
            metrics = cast(dict[str, object], result["metrics"]) if result else {}
            raw_net = metrics.get("trading_net_pnl")
            trading_net = Decimal(str(raw_net)) if raw_net is not None else None
            factor: Decimal | None = Decimal(1)
            if cell.research.cash_unit != config.reporting_currency:
                rates = [
                    r
                    for r in config.fx_rates
                    if r.base_currency == cell.research.cash_unit
                    and r.quote_currency == config.reporting_currency
                    and r.date == cell.research.end_at_utc.date()
                ]
                factor = rates[0].rate if len(rates) == 1 else None
            economic = None
            reasons: list[str] = []
            if result is None:
                reasons.append("cell_not_completed")
            if trading_net is None:
                reasons.append("trading_valuation_unavailable")
            if not config.expenses_complete:
                reasons.append("operating_expenses_not_declared_complete")
            if allocated.total_cost is None:
                reasons.extend(allocated.unavailable_reasons)
            if factor is None:
                reasons.append("missing_cash_unit_fx")
            if not reasons:
                assert (
                    trading_net is not None
                    and factor is not None
                    and allocated.total_cost is not None
                )
                economic = trading_net * factor - allocated.total_cost
            comparable[cell.cell_id] = economic
            trading[cell.cell_id] = trading_net
            counts = cast(dict[str, int], metrics.get("counts", {}))
            samples_ok = (
                result is not None
                and len(cell.research.frames) >= config.minimum_frames
                and counts.get("fills", 0) >= config.minimum_fills
            )
            qualified = (
                samples_ok
                and trading_net is not None
                and not bool(result and result.get("entry_suspended"))
            )
            sufficient = sufficient and qualified and not reasons
            cells.append(
                {
                    "cell_id": cell.cell_id,
                    "run_id": str(cell.research.run_id),
                    "architecture": cell.ai.flow.mode if cell.ai else "rules",
                    "profile": cell.profile.model_dump(mode="json"),
                    "provider": cell.ai.provider if cell.ai else None,
                    "model": cell.ai.model if cell.ai else None,
                    "completed": result is not None,
                    "cash_unit": cell.research.cash_unit,
                    "metrics": metrics,
                    "equity_curve": result["equity_curve"] if result else [],
                    "ai_traces": result["ai_traces"] if result else [],
                    "provenance": {
                        "input_hash": result.get("input_hash") if result else None,
                        "engine_hash": result.get("engine_hash") if result else None,
                        "dataset_hash": result.get("dataset_hash") if result else None,
                        "code_version": cell.research.code_version,
                    },
                    "costs": allocated.model_dump(mode="json"),
                    "ai_metrics": result.get("ai_metrics") if result else None,
                    "economic_net_pnl": str(economic) if economic is not None else None,
                    "economic_roi": str(economic / (cell.research.initial_cash * factor))
                    if economic is not None and factor is not None
                    else None,
                    "economic_basis": "simulated_trading_less_declared_expenses",
                    "unavailable_reasons": reasons,
                    "minimum_sample_met": samples_ok,
                    "entry_suspended": bool(result and result.get("entry_suspended")),
                }
            )
        baseline = comparable.get(config.baseline_cell)
        comparisons = [
            {
                "cell_id": cell.cell_id,
                "baseline_cell": config.baseline_cell,
                "economic_net_delta": _delta(comparable[cell.cell_id], baseline),
                "trading_net_delta": _delta(
                    trading[cell.cell_id], trading.get(config.baseline_cell)
                ),
                "trading_cash_unit": cell.research.cash_unit,
            }
            for cell in config.cells
            if cell.cell_id != config.baseline_cell
        ]
        economic_values = [c["economic_net_pnl"] for c in cells]
        consolidated_net = (
            sum((Decimal(str(v)) for v in economic_values), Decimal(0))
            if all(v is not None for v in economic_values)
            else None
        )
    return {
        "schema_version": "1",
        "experiment_id": str(config.experiment_id),
        "config_hash": config.fingerprint,
        "configuration": config.model_dump(mode="json"),
        "engine_hash": runtime_fingerprint(),
        "dimension": config.dimension,
        "dataset_hash": config.cells[0].research.dataset_hash,
        "start_at_utc": config.cells[0].research.start_at_utc.isoformat(),
        "end_at_utc": config.cells[0].research.end_at_utc.isoformat(),
        "reporting_currency": config.reporting_currency,
        "cells": cells,
        "costs": costs.model_dump(mode="json"),
        "paired_comparisons": comparisons,
        "consolidated_economic_net_pnl": str(consolidated_net)
        if consolidated_net is not None
        else None,
        "evidence_sufficient": sufficient,
        "empirical_performance_claim": False,
        "comparison_count": len(comparisons),
        "limitations": [
            "Synthetic/archived engineering evidence; no genuine investment edge established",
            "Economic results combine simulated trading with explicitly labeled expense evidence",
            "No automatic statistical significance, annualization, model calibration or deployment",
            "Paper approval is an evaluation event, never live authorization",
        ],
    }


def _delta(value: Decimal | None, baseline: Decimal | None) -> str | None:
    return str(value - baseline) if value is not None and baseline is not None else None
