"""The alert rules, defined once. The dashboard shows them; the Telegram sender (slice 8) will use
exactly this module, so the page and the messages cannot disagree.

Thresholds were approved by the owner on 2026-10-29. Pure functions: facts in, findings out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Literal

Severity = Literal["critical", "warning"]

LOOP_STALLED_AFTER = timedelta(minutes=40)
STRATEGY_FAILED_CYCLES = 3
STRATEGY_FAILURE_WINDOW = timedelta(minutes=50)  # three 15-minute cycles, with slack
PRICE_STALE_AFTER = timedelta(minutes=45)
AI_UNKNOWN_OUTCOMES = 3  # in the last 24 hours; the breaker itself stops the AI at 5
AI_BUDGET_WARN = Decimal("0.80")
AI_BUDGET_STOP = Decimal(1)
REPEAT_AFTER = timedelta(hours=6)  # the sender never repeats the same finding sooner than this
YOUNG_RUN = timedelta(hours=72)
CLOSE_RACE_EUR = Decimal(1)

RULES: tuple[dict[str, str], ...] = (
    {
        "id": "R1",
        "name": "The loop stopped",
        "when": "No strategy has recorded a value for 40 minutes (a cycle runs every 15).",
    },
    {
        "id": "R2",
        "name": "A strategy stopped working",
        "when": "One strategy failed 3 cycles in a row (3 errors from it within 50 minutes).",
    },
    {
        "id": "R3",
        "name": "Kraken prices are not arriving",
        "when": "The newest price for any coin is older than 45 minutes.",
    },
    {
        "id": "R4",
        "name": "The AI provider is not answering",
        "when": "3 calls ended with an unknown outcome in 24 hours (at 5 the AI pauses itself).",
    },
    {
        "id": "R5",
        "name": "AI budget",
        "when": "80% of the total AI budget used (warning) and 100% (the AI stops for good).",
    },
    {
        "id": "R6",
        "name": "Unexpected error",
        "when": "The code raised an error nobody has acknowledged yet.",
    },
)


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: Severity
    title: str
    detail: str
    key: str  # same key = same problem, so a sender can avoid repeating it
    strategy_id: str | None = None


@dataclass(frozen=True)
class Facts:
    loop_age: timedelta | None  # newest equity point across strategies; None = nothing yet
    strategy_failures: dict[str, int] = field(default_factory=dict)
    price_age: dict[str, timedelta | None] = field(default_factory=dict)  # per pair
    ai_active: bool = False
    ai_unknown_24h: int = 0
    ai_breaker_open: bool = False
    ai_budget_used: Decimal = Decimal(0)  # fraction of the total limit, 0 to 1+
    unacknowledged_errors: int = 0


def evaluate(facts: Facts) -> list[Finding]:
    found: list[Finding] = []
    if facts.loop_age is not None and facts.loop_age > LOOP_STALLED_AFTER:
        minutes = int(facts.loop_age.total_seconds() // 60)
        found.append(
            Finding(
                "R1",
                "critical",
                "The loop has stopped",
                f"Nothing was recorded for {minutes} minutes. The server, the container or the "
                "database may be down.",
                "loop",
            )
        )
    for strategy_id, failures in sorted(facts.strategy_failures.items()):
        if failures >= STRATEGY_FAILED_CYCLES:
            found.append(
                Finding(
                    "R2",
                    "critical",
                    "A strategy stopped working",
                    f"{strategy_id} failed {failures} cycles in a row. Open the Errors page.",
                    f"strategy:{strategy_id}",
                    strategy_id,
                )
            )
    for pair, age in sorted(facts.price_age.items()):
        if age is None or age > PRICE_STALE_AFTER:
            when = (
                "has never arrived"
                if age is None
                else f"is {int(age.total_seconds() // 60)} min old"
            )
            found.append(
                Finding(
                    "R3",
                    "critical",
                    "Kraken prices are not arriving",
                    f"The newest {pair} price {when}. Strategies skip a coin they cannot price.",
                    f"prices:{pair}",
                )
            )
    if facts.ai_active:
        if facts.ai_breaker_open:
            found.append(
                Finding(
                    "R4",
                    "critical",
                    "The AI paused itself",
                    "Too many calls ended with an unknown outcome. It resumes by itself "
                    "(see the AI page for when).",
                    "ai:breaker",
                )
            )
        elif facts.ai_unknown_24h >= AI_UNKNOWN_OUTCOMES:
            found.append(
                Finding(
                    "R4",
                    "warning",
                    "The AI provider is not answering reliably",
                    f"{facts.ai_unknown_24h} calls in the last 24 h had an unknown outcome.",
                    "ai:provider",
                )
            )
        if facts.ai_budget_used >= AI_BUDGET_STOP:
            found.append(
                Finding(
                    "R5",
                    "critical",
                    "AI budget used up",
                    "The AI will not ask again. It does not resume by itself.",
                    "ai:budget:100",
                )
            )
        elif facts.ai_budget_used >= AI_BUDGET_WARN:
            found.append(
                Finding(
                    "R5",
                    "warning",
                    "AI budget nearly used",
                    f"{int(facts.ai_budget_used * 100)}% of the total budget is used.",
                    "ai:budget:80",
                )
            )
    if facts.unacknowledged_errors:
        found.append(
            Finding(
                "R6",
                "warning",
                "Unexpected error to review",
                f"{facts.unacknowledged_errors} error(s) wait for you on the Errors page.",
                "errors",
            )
        )
    return found
