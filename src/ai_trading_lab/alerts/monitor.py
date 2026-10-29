"""Decide which alerts to send, then send them through the durable single-attempt outbox.

The decision (`plan`) is a pure function of the findings, the stored state and the clock:

* a finding that is not active yet -> tell the owner once ("fire");
* still firing and the last message is older than 6 hours -> remind once ("repeat");
* firing but never delivered (Telegram failed) -> try again on the next check, at most 5 times,
  then wait the usual 6 hours;
* no longer firing and the owner had been told -> one short "resolved" message.

Telegram is the only destination and the message carries only our own fixed text: a rule title, a
detail sentence built by `dashboard/alerts.py`, the rule id. Nothing from the market, the AI or an
exception message is ever copied into it.
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

MAX_FAILED_SENDS = 5
FORGET_AFTER = timedelta(days=14)
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
            if row.failed_sends < MAX_FAILED_SENDS:
                out.append(
                    Action("repeat", **base, episode=row.episode, first_fired=row.first_fired_utc)
                )
        elif now - row.last_sent_utc >= alerts.REPEAT_AFTER:
            out.append(
                Action("repeat", **base, episode=row.episode, first_fired=row.first_fired_utc)
            )
    for key, row in sorted(rows.items()):
        if row.resolved_at_utc is None and key not in firing_keys and row.last_sent_utc is not None:
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
        event_key=f"heartbeat:{now.strftime('%Y%m%d')}",
        kind="daily_summary",
        occurred_at_utc=now,
        text=text,
    )


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
        await self.outbox.enqueue(event, now)
        result = await self.outbox.dispatch(event.event_key, self.adapter, now)
        return bool(result["state"] == "delivered")

    async def tick(self, overview: dict[str, Any], now: datetime) -> dict[str, int]:
        """One check. Returns counts for the log line; never raises for a Telegram problem."""
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
        for action in plan(now, findings, rows):
            try:
                delivered = await self._deliver(notification(action, now), now)
            except ValueError:
                delivered = False  # a message the safety checks refuse must not block the others
            counts["sent" if delivered else "failed"] += 1
            old = rows.get(action.key)
            if action.verb == "resolve":
                # Resolved either way: a missed "resolved" note must not be retried forever.
                if old is not None:
                    await self.state.save(replace(old, resolved_at_utc=now))
                continue
            failed = (
                0 if delivered else (old.failed_sends + 1 if old and action.verb == "repeat" else 1)
            )
            row = Row(
                key=action.key,
                rule=action.rule,
                severity=action.severity,
                title=action.title,
                detail=action.detail,
                first_fired_utc=action.first_fired,
                last_sent_utc=now if delivered else (old.last_sent_utc if old else None),
                resolved_at_utc=None,
                episode=action.episode,
                failed_sends=failed,
            )
            if not delivered and failed >= MAX_FAILED_SENDS:
                row = replace(row, last_sent_utc=now)  # give up for one 6 hour window
            await self.state.save(row)
        await self.state.forget_resolved_before(now - FORGET_AFTER)
        await self._heartbeat(overview, now, counts)
        return counts

    async def _heartbeat(
        self, overview: dict[str, Any], now: datetime, counts: dict[str, int]
    ) -> None:
        if self.heartbeat_hour_utc is None or now.hour < self.heartbeat_hour_utc:
            return
        event = heartbeat(overview, now)
        try:
            known = await self.outbox.get(event.event_key)
            if known["state"] != "pending":
                return  # today's message was already attempted; never send a day's twice
        except ValueError:
            pass
        counts["sent" if await self._deliver(event, now) else "failed"] += 1


def log(event: dict[str, Any]) -> None:
    print(json.dumps(event, sort_keys=True, default=str), file=sys.stdout, flush=True)
