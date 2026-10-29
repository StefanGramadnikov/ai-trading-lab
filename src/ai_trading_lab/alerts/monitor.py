"""Decide which alerts to send, then send them through the durable single-attempt outbox.

The decision (`plan`) is a pure function of the findings, the stored state and the clock:

* a finding that is not active yet -> tell the owner once ("fire");
* still firing and the last message is older than 6 hours -> remind once ("repeat");
* firing but never delivered (Telegram failed) -> try again after a growing pause
  (5 min, 15 min, 1 h, 3 h, 6 h), never faster;
* no longer firing for 30 minutes and the owner had been told -> one short "resolved" message.
  A problem that comes and goes inside that half hour stays one episode, so a value hovering at a
  threshold cannot flood the chat.

Each send is recorded as "told" *before* it is attempted and rolled back if it failed: a crash in
between can lose one message but can never send a second copy. Telegram's own "unknown" outcome
(timeout, 5xx) counts as a failure and is retried, so in that rare case a duplicate is possible;
for an alert that is the better side of the trade (the single-attempt rule protects orders, not
messages).

Telegram is the only destination and the message carries only fixed text from our own code: a rule
title, a detail sentence built by `dashboard/alerts.py`, the rule id. Nothing from the market, the
AI or an exception message is ever copied into it.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Literal

from ai_trading_lab.alerts.state import AlertState, Row
from ai_trading_lab.dashboard import alerts
from ai_trading_lab.notifications.models import Kind, Notification
from ai_trading_lab.notifications.store import NotificationStore
from ai_trading_lab.notifications.telegram import TelegramAdapter

BACKOFF = tuple(timedelta(minutes=m) for m in (5, 15, 60, 180, 360))
QUIET_BEFORE_RESOLVE = timedelta(minutes=30)
FORGET_AFTER = timedelta(days=14)
HEARTBEAT_KEY = "heartbeat"
DASHBOARD_HINT = (
    "Open the dashboard: ssh -L 8000:localhost:8000 atl@SERVER, then http://localhost:8000"
)

Verb = Literal["fire", "repeat", "resolve"]


@dataclass(frozen=True)
class Action:
    verb: Verb
    key: str
    rule: str
    severity: str
    title: str
    detail: str
    episode: int
    first_fired: datetime


def retry_due(now: datetime, row: Row) -> bool:
    """After failed sends, wait the backoff for that many failures before trying again."""
    if row.failed_sends == 0 or row.last_attempt_utc is None:
        return True
    return now - row.last_attempt_utc >= BACKOFF[min(row.failed_sends, len(BACKOFF)) - 1]


def plan(now: datetime, firing: list[alerts.Finding], rows: dict[str, Row]) -> list[Action]:
    out: list[Action] = []
    firing_keys = {f.key for f in firing}
    for finding in firing:
        row = rows.get(finding.key)
        base = dict(
            key=finding.key,
            rule=finding.rule,
            severity=finding.severity,
            title=finding.title,
            detail=finding.detail,
        )
        if row is None or row.resolved_at_utc is not None:
            episode = row.episode + 1 if row else 1
            out.append(Action("fire", **base, episode=episode, first_fired=now))
        elif row.last_sent_utc is None:
            if retry_due(now, row):  # the owner was never told: still a first message
                out.append(
                    Action("fire", **base, episode=row.episode, first_fired=row.first_fired_utc)
                )
        elif now - row.last_sent_utc >= alerts.REPEAT_AFTER and retry_due(now, row):
            out.append(
                Action("repeat", **base, episode=row.episode, first_fired=row.first_fired_utc)
            )
    for key, row in sorted(rows.items()):
        if (
            row.resolved_at_utc is None
            and key not in firing_keys
            and row.last_sent_utc is not None
            and now - row.last_firing_utc >= QUIET_BEFORE_RESOLVE
        ):
            out.append(
                Action(
                    "resolve",
                    key,
                    row.rule,
                    row.severity,
                    row.title,
                    row.detail,
                    row.episode,
                    row.first_fired_utc,
                )
            )
    return out


def _stamp(now: datetime) -> str:
    return now.strftime("%Y%m%dT%H%M%SZ")


def notification(action: Action, now: datetime) -> Notification:
    if action.verb == "resolve":
        kind: Kind = "recovery"
        text = f"Resolved: {action.title} (rule {action.rule}). It is no longer happening."
    else:
        kind = "budget_warning" if action.rule == "R5" else "failure"
        label = "CRITICAL" if action.severity == "critical" else "Warning"
        again = " Reminder: still not fixed." if action.verb == "repeat" else ""
        text = (
            f"{label}: {action.title} (rule {action.rule}).{again}\n{action.detail}\n"
            f"{DASHBOARD_HINT}"
        )
    return Notification(
        event_key=f"alert:{action.key}:{action.episode}:{action.verb}:{_stamp(now)}",
        kind=kind,
        occurred_at_utc=now,
        text=text,
    )


def heartbeat(overview: dict[str, Any], now: datetime) -> Notification:
    """The daily "still alive" message: silence must never be the only sign of a dead alerter."""
    parts = [
        f"{s['label']} {s['equity']} EUR" if s["equity"] else f"{s['label']} no data"
        for s in overview["strategies"]
    ]
    ai = overview["ai"]
    firing = len(overview["alerts"]["firing"])
    text = (
        "Daily check: the system is running. Values (start 100 EUR): "
        + "; ".join(parts)
        + f". Alerts active now: {firing}. Errors to review: "
        f"{overview['errors']['unacknowledged_errors']}. AI: "
        + (
            f"{ai['state']}, USD {ai['spent_estimated']} of {ai['total_limit']} spent"
            if ai["active"]
            else "off"
        )
        + "."
    )
    return Notification(
        event_key=f"heartbeat:{_stamp(now)}", kind="daily_summary", occurred_at_utc=now, text=text
    )


class DatabaseWatch:
    """The outbox lives in the database, so when that is unreachable this path keeps its state in
    memory and sends straight through the adapter: one message per 6 hours, one when it is back."""

    def __init__(self) -> None:
        self.last_notice: datetime | None = None
        self.told = False

    async def down(self, adapter: TelegramAdapter | None, now: datetime) -> bool:
        if adapter is None or not adapter.enabled:
            return False
        if self.last_notice is not None and now - self.last_notice < alerts.REPEAT_AFTER:
            return False
        event = Notification(
            event_key=f"db:down:{_stamp(now)}",
            kind="failure",
            occurred_at_utc=now,
            text=(
                "CRITICAL: the database does not answer, so the alerts and the dashboard cannot "
                "check anything and the trading loop may be stuck too.\n"
                "On the server: docker compose ps, then docker compose logs --tail 50 db."
            ),
        )
        delivered = (await adapter.send(event)).state == "delivered"
        if delivered:
            self.last_notice, self.told = now, True
        return delivered

    async def up(self, adapter: TelegramAdapter | None, now: datetime) -> None:
        if not self.told or adapter is None or not adapter.enabled:
            return
        event = Notification(
            event_key=f"db:up:{_stamp(now)}",
            kind="recovery",
            occurred_at_utc=now,
            text="Resolved: the database answers again.",
        )
        await adapter.send(event)
        self.told, self.last_notice = False, None


class AlertRunner:
    def __init__(
        self,
        state: AlertState,
        outbox: NotificationStore,
        adapter: TelegramAdapter | None,
        *,
        heartbeat_hour_utc: int | None = 7,
    ) -> None:
        self.state, self.outbox, self.adapter = state, outbox, adapter
        self.heartbeat_hour_utc = heartbeat_hour_utc

    @property
    def sending(self) -> bool:
        return self.adapter is not None and self.adapter.enabled

    async def _deliver(self, event: Notification, now: datetime) -> bool:
        assert self.adapter is not None
        try:
            await self.outbox.enqueue(event, now)
            result = await self.outbox.dispatch(event.event_key, self.adapter, now)
        except Exception:  # noqa: BLE001 - one message failing must not stop the others
            return False
        return bool(result["state"] == "delivered")

    async def deliver_text(self, event: Notification) -> bool:
        """Send one prepared message through the outbox (used by the daily review)."""
        return await self._deliver(event, event.occurred_at_utc)

    async def tick(self, overview: dict[str, Any], now: datetime) -> dict[str, int]:
        """One check. Returns counts for the log line; a Telegram or database problem with one
        message never stops the rest."""
        counts = {"sent": 0, "failed": 0}
        if not self.sending:
            return counts  # nothing is marked as told while nothing can be told
        findings = [
            alerts.Finding(
                rule=f["rule"],
                severity=f["severity"],
                title=f["title"],
                detail=f["detail"],
                key=f["key"],
                strategy_id=f.get("strategy_id"),
            )
            for f in overview["alerts"]["firing"]
        ]
        rows = await self.state.rows()
        actions = plan(now, findings, rows)
        acted = {a.key for a in actions}
        for action in actions:
            try:
                delivered = await self._act(action, rows.get(action.key), now)
            except Exception:  # noqa: BLE001
                delivered = False
            counts["sent" if delivered else "failed"] += 1
        # Remember that quiet findings are still firing, and close the ones nobody was told about.
        firing_keys = {f.key for f in findings}
        for key, row in rows.items():
            if row.resolved_at_utc is not None or key in acted:
                continue
            if key in firing_keys:
                await self.state.save(replace(row, last_firing_utc=now))
            elif row.last_sent_utc is None:
                await self.state.save(replace(row, resolved_at_utc=now))
        await self.state.forget_resolved_before(now - FORGET_AFTER)
        try:
            await self._heartbeat(overview, now, counts)
        except Exception:  # noqa: BLE001
            counts["failed"] += 1
        return counts

    async def _act(self, action: Action, old: Row | None, now: datetime) -> bool:
        active = old if old is not None and old.resolved_at_utc is None else None
        if action.verb == "resolve":
            # Closed first, so a failed note is never retried forever.
            if old is not None:
                await self.state.save(replace(old, resolved_at_utc=now))
            return await self._deliver(notification(action, now), now)
        row = Row(
            key=action.key,
            rule=action.rule,
            severity=action.severity,
            title=action.title,
            detail=action.detail,
            first_fired_utc=action.first_fired,
            last_firing_utc=now,
            last_sent_utc=now,  # recorded as told before trying: a crash can lose, never repeat
            last_attempt_utc=now,
            resolved_at_utc=None,
            episode=action.episode,
            failed_sends=0,
        )
        await self.state.save(row)
        delivered = await self._deliver(notification(action, now), now)
        if not delivered:
            await self.state.save(
                replace(
                    row,
                    last_sent_utc=active.last_sent_utc if active else None,
                    failed_sends=(active.failed_sends if active else 0) + 1,
                )
            )
        return delivered

    async def _heartbeat(
        self, overview: dict[str, Any], now: datetime, counts: dict[str, int]
    ) -> None:
        if self.heartbeat_hour_utc is None or now.hour < self.heartbeat_hour_utc:
            return
        rows = await self.state.rows()
        old = rows.get(HEARTBEAT_KEY)
        if (
            old is not None
            and old.last_sent_utc is not None
            and old.last_sent_utc.date() == now.date()
        ):
            return  # today's check is done
        if old is not None and not retry_due(now, old):
            return
        failed = old.failed_sends if old else 0
        base = Row(
            key=HEARTBEAT_KEY,
            rule="HB",
            severity="info",
            title="Daily check",
            detail="",
            first_fired_utc=now,
            last_firing_utc=now,
            last_sent_utc=old.last_sent_utc if old else None,
            last_attempt_utc=now,
            resolved_at_utc=now,  # never an "active alert": the planner ignores it
            episode=1,
            failed_sends=failed,
        )
        delivered = await self._deliver(heartbeat(overview, now), now)
        counts["sent" if delivered else "failed"] += 1
        await self.state.save(
            replace(base, last_sent_utc=now, failed_sends=0)
            if delivered
            else replace(base, failed_sends=failed + 1)
        )


def log(event: dict[str, Any]) -> None:
    print(json.dumps(event, sort_keys=True, default=str), file=sys.stdout, flush=True)
