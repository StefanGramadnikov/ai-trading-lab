"""A bounded, redacted journal of everything that went wrong, readable by people and the dashboard.

Exceptions the loop contains (so it keeps running) used to be reduced to a type name. This keeps
what is needed to fix them: a stable fingerprint, counts, first and last seen, the strategy and
pair, a redacted message and a redacted stack trace. It is deliberately small: one row per distinct
problem plus a few recent occurrences, so a failure that repeats every cycle cannot grow the
database.

Recording never raises. A broken journal must not take the trading loop down with it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
import time
import traceback
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import unquote

from pydantic import AwareDatetime
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Identity,
    Index,
    Integer,
    String,
    Table,
    Text,
    case,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_trading_lab.market_data.models import FrozenModel
from ai_trading_lab.market_data.storage import metadata

Level = Literal["error", "warning"]
Status = Literal["new", "seen", "acknowledged"]
COMPONENTS = (
    "data_collection",
    "rules_strategy",
    "ai_strategy",
    "ai_provider",
    "benchmark_strategy",
    "paper_broker",
    "worker",
    "database",
    "dashboard",
)

MAX_MESSAGE = 500
MAX_TRACE = 6000
MAX_CONTEXT = 2000
MAX_FRAMES = 30
PRE_CLIP = 20000  # nothing longer is ever run through the patterns
WRITE_TIMEOUT = 5.0
BACKOFF_SECONDS = 60.0
KEEP_OCCURRENCES = 20  # per problem
KEEP_DAYS = 30
MAX_GROUPS = 500
HARD_MAX_GROUPS = 1000  # even unacknowledged errors are capped, oldest first
PRUNE_EVERY = timedelta(hours=1)

error_groups = Table(
    "error_groups",
    metadata,
    Column("fingerprint", String(64), primary_key=True),
    Column("level", String(10), nullable=False),
    Column("component", String(30), nullable=False),
    Column("error_type", String(100), nullable=False),
    Column("location", String(200), nullable=False),
    Column("headline", String(200), nullable=False),
    Column("status", String(14), nullable=False),
    Column("count", Integer, nullable=False),
    Column("first_seen_utc", DateTime(timezone=True), nullable=False),
    Column("last_seen_utc", DateTime(timezone=True), nullable=False),
    Column("last_strategy_id", String(100)),
    Column("last_pair", String(12)),
    Column("last_context", JSONB),
    Column("last_message", String(MAX_MESSAGE)),
    Column("last_traceback", Text),
    Column("acknowledged_by", String(100)),
    Column("acknowledged_at_utc", DateTime(timezone=True)),
    Column("acknowledged_reason", String(200)),
)
Index("ix_error_groups_last_seen", error_groups.c.last_seen_utc)
error_occurrences = Table(
    "error_occurrences",
    metadata,
    Column("seq", BigInteger, Identity(always=True), primary_key=True),
    Column("fingerprint", String(64), nullable=False),
    Column("at_utc", DateTime(timezone=True), nullable=False),
    Column("strategy_id", String(100)),
    Column("pair", String(12)),
    Column("context", JSONB),
    Column("message", String(MAX_MESSAGE)),
)
Index("ix_error_occurrences_fp", error_occurrences.c.fingerprint, error_occurrences.c.seq)


class ErrorGroup(FrozenModel):
    fingerprint: str
    level: Level
    component: str
    error_type: str
    location: str
    headline: str
    status: Status
    count: int
    first_seen_utc: AwareDatetime
    last_seen_utc: AwareDatetime
    last_strategy_id: str | None = None
    last_pair: str | None = None
    last_context: dict[str, Any] | None = None
    last_message: str | None = None
    last_traceback: str | None = None
    acknowledged_by: str | None = None
    acknowledged_at_utc: AwareDatetime | None = None
    acknowledged_reason: str | None = None


class Occurrence(FrozenModel):
    seq: int
    fingerprint: str
    at_utc: AwareDatetime
    strategy_id: str | None = None
    pair: str | None = None
    context: dict[str, Any] | None = None
    message: str | None = None


class JournalSummary(FrozenModel):
    unacknowledged_errors: int
    errors_24h: int
    warnings_24h: int
    last_error_utc: AwareDatetime | None = None


_SECRET_WORDS = (
    r"api[_-]?key|token|secret|passw(?:or)?d|passphrase|key|sign(?:ature)?|auth\w*|cookie|session"
)
_REDACTIONS = (
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "sk-[redacted]"),
    # Whole header lines: the scheme word ("Basic", "Bearer") and the value go together.
    (
        re.compile(
            r"(?i)\b(authorization|proxy-authorization|cookie|set-cookie|api-sign)\b"
            r"[ \t]*[:=][^\r\n]*"
        ),
        r"\1: [redacted]",
    ),
    (re.compile(r"(?i)\bbearer\s+\S{8,}"), "Bearer [redacted]"),
    # name=value or "name": "value" where the name contains a secret-looking word, even inside
    # longer names such as OPENAI_API_KEY or client_secret.
    (
        re.compile(
            r"(?i)([A-Za-z0-9_\-]*(?:" + _SECRET_WORDS + r")[A-Za-z0-9_\-]*)"
            r"([\"']?[ \t]*[=:][ \t]*[\"']?)[^\s\"']+"
        ),
        r"\1\2[redacted]",
    ),
    # Credentials in a URL, up to the last @ of the token so a / or @ in the password cannot leak.
    (re.compile(r"(://[^:/\s@]+:)\S*@"), r"\1[redacted]@"),
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "[redacted-hex]"),
)
_TOKENISH = re.compile(r"[A-Za-z0-9+/_\-]{32,}={0,2}")


def _mask_token(match: re.Match[str]) -> str:
    """A long run that mixes digits, upper and lower case is a key or token, not a path or name."""
    value = match.group(0)
    if (
        any(c.isdigit() for c in value)
        and any(c.isupper() for c in value)
        and any(c.islower() for c in value)
    ):
        return "[redacted-long-token]"
    return value


def redact(text: str) -> str:
    """Remove anything that looks like a credential before it is stored or shown."""
    text = text.replace("\x00", "")[:PRE_CLIP]
    if "%" in text:
        text = unquote(text)  # token%3Dabc and %22api_key%22%3A are the same secrets
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return _TOKENISH.sub(_mask_token, text)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _safe(text: str, limit: int) -> str:
    return _clip(redact(text), limit)


def _safe_context(context: dict[str, Any] | None) -> dict[str, Any] | None:
    """Context is redacted as text and bounded; anything odd is replaced by a marker."""
    if context is None:
        return None
    try:
        text = redact(json.dumps(context, default=str))
        if len(text) > MAX_CONTEXT:
            return {"truncated": True}
        loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else {"value": loaded}
    except (TypeError, ValueError):
        return {"unserializable": True}


def _trace(exc: BaseException) -> str:
    """The frames (never locals) and a clipped message, built without formatting a huge message."""
    frames = "".join(
        traceback.format_list(traceback.extract_tb(exc.__traceback__, limit=-MAX_FRAMES))
    )
    last = f"{type(exc).__name__}: {str(exc)[:PRE_CLIP]}"
    text = redact("Traceback (most recent call last):\n" + frames + last)
    if len(text) <= MAX_TRACE:
        return text
    head = MAX_TRACE // 3
    return text[:head] + "\n...[trace shortened]...\n" + text[-(MAX_TRACE - head - 30) :]


def _location(exc: BaseException) -> str:
    """The innermost frame in our own code, without line numbers (edits keep one fingerprint)."""
    ours = fallback = ""
    for frame in traceback.extract_tb(exc.__traceback__):
        name = frame.filename.rsplit("/", 1)[-1].removesuffix(".py")
        fallback = f"{name}:{frame.name}"
        if "ai_trading_lab/" in frame.filename:
            module = frame.filename.split("ai_trading_lab/")[-1].removesuffix(".py")
            ours = f"{module.replace('/', '.')}:{frame.name}"
    return (ours or fallback or "unknown")[:200]


def fingerprint(component: str, error_type: str, location: str, level: str = "error") -> str:
    return hashlib.sha256(f"{level}|{component}|{error_type}|{location}".encode()).hexdigest()


class ErrorJournal:
    def __init__(self, engine: AsyncEngine, *, clock: Any = None) -> None:
        self.engine = engine
        self._clock = clock or (lambda: datetime.now(UTC))
        self._last_prune: datetime | None = None
        self._paused_until = 0.0

    # ---- recording (never raises) -----------------------------------------------------

    async def record_exception(
        self,
        component: str,
        exc: BaseException,
        *,
        strategy_id: str | None = None,
        pair: str | None = None,
        context: dict[str, Any] | None = None,
        at: datetime | None = None,
    ) -> bool:
        try:
            error_type = type(exc).__name__
            location = _location(exc)
            fields: dict[str, Any] = {
                "level": "error",
                "component": component,
                "error_type": error_type,
                "location": location,
                "headline": f"{error_type} in {location}",
                "message": str(exc)[:PRE_CLIP],
                "trace": _trace(exc),
                "strategy_id": strategy_id,
                "pair": pair,
                "context": context,
                "at": at,
            }
        except Exception as problem:  # the journal must never break the loop
            print(f"error journal failed: {type(problem).__name__}", file=sys.stderr)
            return False
        return await self._guarded(fields)

    async def record_event(
        self,
        component: str,
        error_type: str,
        message: str = "",
        *,
        level: Level = "warning",
        location: str = "",
        headline: str | None = None,
        strategy_id: str | None = None,
        pair: str | None = None,
        context: dict[str, Any] | None = None,
        at: datetime | None = None,
    ) -> bool:
        """A problem with no exception behind it (Kraken not answering, an AI call timing out)."""
        return await self._guarded(
            {
                "level": level,
                "component": component,
                "error_type": error_type,
                "location": location,
                "headline": headline or f"{error_type} ({component})",
                "message": message[:PRE_CLIP],
                "trace": None,
                "strategy_id": strategy_id,
                "pair": pair,
                "context": context,
                "at": at,
            }
        )

    async def _guarded(self, fields: dict[str, Any]) -> bool:
        """Sanitise, bound the time spent, and stand down for a minute after a failure.

        A database outage must cost the loop at most one short wait, not one per recorded problem.
        """
        if time.monotonic() < self._paused_until:
            return False
        try:
            clean = {
                **fields,
                "component": str(fields["component"])[:30],
                "error_type": _safe(str(fields["error_type"]), 100),
                "location": _safe(str(fields["location"]), 200),
                "headline": _safe(str(fields["headline"]), 200),
                "message": _safe(str(fields["message"]), MAX_MESSAGE),
                "strategy_id": (fields["strategy_id"] or None) and str(fields["strategy_id"])[:100],
                "pair": (fields["pair"] or None) and str(fields["pair"])[:12],
                "context": _safe_context(fields["context"]),
            }
            await asyncio.wait_for(self._write(**clean), WRITE_TIMEOUT)
        except Exception as problem:
            self._paused_until = time.monotonic() + BACKOFF_SECONDS
            print(f"error journal failed: {type(problem).__name__}", file=sys.stderr)
            return False
        return True

    async def _write(
        self,
        *,
        level: Level,
        component: str,
        error_type: str,
        location: str,
        headline: str,
        message: str,
        trace: str | None,
        strategy_id: str | None,
        pair: str | None,
        context: dict[str, Any] | None,
        at: datetime | None,
    ) -> None:
        now = at or self._clock()
        fp = fingerprint(component, error_type, location, level)
        first_status: Status = "new" if level == "error" else "seen"
        async with self.engine.begin() as connection:
            statement = insert(error_groups).values(
                fingerprint=fp,
                level=level,
                component=component,
                error_type=error_type,
                location=location,
                headline=headline,
                status=first_status,
                count=1,
                first_seen_utc=now,
                last_seen_utc=now,
                last_strategy_id=strategy_id,
                last_pair=pair,
                last_context=context,
                last_message=message,
                last_traceback=trace,
            )
            await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[error_groups.c.fingerprint],
                    set_={
                        "count": error_groups.c.count + 1,
                        "last_seen_utc": now,
                        "last_strategy_id": strategy_id,
                        "last_pair": pair,
                        "last_context": context,
                        "last_message": message,
                        "last_traceback": trace,
                        # An acknowledged problem that happens again needs their eyes again.
                        "status": text_case(error_groups.c.status, level),
                    },
                )
            )
            await connection.execute(
                insert(error_occurrences).values(
                    fingerprint=fp,
                    at_utc=now,
                    strategy_id=strategy_id,
                    pair=pair,
                    context=context,
                    message=message,
                )
            )
            keep = (
                select(error_occurrences.c.seq)
                .where(error_occurrences.c.fingerprint == fp)
                .order_by(error_occurrences.c.seq.desc())
                .limit(KEEP_OCCURRENCES)
            )
            await connection.execute(
                delete(error_occurrences).where(
                    error_occurrences.c.fingerprint == fp,
                    error_occurrences.c.seq.not_in(keep),
                )
            )
        try:
            await self._prune(now)
        except Exception as problem:  # housekeeping must not lose a record that is already saved
            print(f"error journal prune failed: {type(problem).__name__}", file=sys.stderr)

    async def _prune(self, now: datetime) -> None:
        if self._last_prune is not None and now - self._last_prune < PRUNE_EVERY:
            return
        self._last_prune = now
        cutoff = now - timedelta(days=KEEP_DAYS)
        async with self.engine.begin() as connection:
            # Old handled problems go; an unacknowledged error always stays.
            await connection.execute(
                delete(error_groups).where(
                    error_groups.c.last_seen_utc < cutoff, error_groups.c.status != "new"
                )
            )
            total = await connection.scalar(select(func.count()).select_from(error_groups))
            if (total or 0) > MAX_GROUPS:
                oldest = (
                    select(error_groups.c.fingerprint)
                    .where(error_groups.c.status != "new")
                    .order_by(error_groups.c.last_seen_utc)
                    .limit((total or 0) - MAX_GROUPS)
                )
                await connection.execute(
                    delete(error_groups).where(error_groups.c.fingerprint.in_(oldest))
                )
            total = await connection.scalar(select(func.count()).select_from(error_groups))
            if (total or 0) > HARD_MAX_GROUPS:
                # Even unacknowledged errors are capped, oldest first, so growth has a hard bound.
                oldest = (
                    select(error_groups.c.fingerprint)
                    .order_by(error_groups.c.last_seen_utc)
                    .limit((total or 0) - HARD_MAX_GROUPS)
                )
                await connection.execute(
                    delete(error_groups).where(error_groups.c.fingerprint.in_(oldest))
                )
            await connection.execute(
                delete(error_occurrences).where(
                    error_occurrences.c.fingerprint.not_in(select(error_groups.c.fingerprint))
                )
            )

    # ---- reading ----------------------------------------------------------------------

    async def groups(
        self,
        *,
        status: Status | None = None,
        level: Level | None = None,
        component: str | None = None,
        limit: int = 50,
    ) -> list[ErrorGroup]:
        statement = select(error_groups).order_by(error_groups.c.last_seen_utc.desc())
        if status is not None:
            statement = statement.where(error_groups.c.status == status)
        if level is not None:
            statement = statement.where(error_groups.c.level == level)
        if component is not None:
            statement = statement.where(error_groups.c.component == component)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement.limit(limit))).mappings().all()
        return [ErrorGroup.model_validate(dict(r)) for r in rows]

    async def group(self, fingerprint_: str) -> ErrorGroup | None:
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        select(error_groups).where(
                            error_groups.c.fingerprint.like(fingerprint_ + "%")
                        )
                    )
                )
                .mappings()
                .all()
            )
        return ErrorGroup.model_validate(dict(rows[0])) if len(rows) == 1 else None

    async def occurrences(self, fingerprint_: str, limit: int = 20) -> list[Occurrence]:
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        select(error_occurrences)
                        .where(error_occurrences.c.fingerprint == fingerprint_)
                        .order_by(error_occurrences.c.seq.desc())
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
        return [Occurrence.model_validate(dict(r)) for r in rows]

    async def summary(self, now: datetime | None = None) -> JournalSummary:
        moment = now or self._clock()
        since = moment - timedelta(hours=24)
        async with self.engine.connect() as connection:
            unack = await connection.scalar(
                select(func.count())
                .select_from(error_groups)
                .where(error_groups.c.level == "error", error_groups.c.status == "new")
            )
            recent = (
                await connection.execute(
                    select(error_groups.c.level, func.count())
                    .where(error_groups.c.last_seen_utc >= since)
                    .group_by(error_groups.c.level)
                )
            ).all()
            last = await connection.scalar(
                select(func.max(error_groups.c.last_seen_utc)).where(
                    error_groups.c.level == "error"
                )
            )
        by_level = {level: count for level, count in recent}
        return JournalSummary(
            unacknowledged_errors=int(unack or 0),
            errors_24h=int(by_level.get("error", 0)),
            warnings_24h=int(by_level.get("warning", 0)),
            last_error_utc=last,
        )

    async def acknowledge(
        self,
        fingerprint_: str | None,
        *,
        actor: str,
        reason: str,
        at: datetime | None = None,
    ) -> int:
        """Acknowledge one problem (by fingerprint prefix) or, with None, every new error."""
        now = at or self._clock()
        condition = error_groups.c.status == "new"
        if fingerprint_ is not None:
            matching = error_groups.c.fingerprint.startswith(fingerprint_, autoescape=True)
            condition = condition & matching
        async with self.engine.begin() as connection:
            if fingerprint_ is not None:
                found = await connection.scalar(
                    select(func.count()).select_from(error_groups).where(matching)
                )
                if (found or 0) > 1:
                    raise ValueError("ambiguous fingerprint prefix")
            done = await connection.execute(
                update(error_groups)
                .where(condition)
                .values(
                    status="acknowledged",
                    acknowledged_by=actor[:100],
                    acknowledged_at_utc=now,
                    acknowledged_reason=reason[:200],
                )
            )
        return int(done.rowcount or 0)


def text_case(status_column: Any, level: str) -> Any:
    """Status on a repeat: a warning stays `seen`; an acknowledged error becomes `new` again."""
    if level != "error":
        return "seen"
    return case((status_column == "acknowledged", "new"), else_=status_column)


__all__ = [
    "COMPONENTS",
    "ErrorGroup",
    "ErrorJournal",
    "JournalSummary",
    "Occurrence",
    "fingerprint",
    "redact",
]
