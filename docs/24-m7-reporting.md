# 24 — M7 reporting and notifications

Approved by the user on 2026-10-20: “yes, carry on”, following the six-step M7 plan.
Base merged M6 `1b401856635e6906d6af73c5a1b21683a14f0c96`;
branch `feature/m7-reporting`. Implementation only; `/ship` remains separate.

## Contracts and increments

1. Read-only bounded experiment list/detail/status API. Reuse M6 verified reports;
   no independent financial recomputation or defaulting unknown values to zero.
   Historical runtime-incompatible entries show identity/compatibility only;
   detail/export refuses them rather than bypassing replay integrity. No migration
   of old reports, mutable risk settings or experiment controls in the web app.
2. Local HTML/CSS/JavaScript dashboard in the existing FastAPI container, no CDN
   or frontend build service. Responsive, keyboard usable, light/dark styles,
   loading/empty/error states, exact decimal strings, explicit simulated evidence,
   missing metrics and freshness timestamps. SVG curves are presentation only.
3. HTTP Basic single-user credentials from ignored environment configuration;
   missing credentials fail closed. Protect reporting, static assets and exports.
   Keep existing health contracts. Compose stays loopback-only; HTTPS/remote access
   and restrictive database roles belong to M8. No browser credential storage.
4. Bounded JSON audit export with report/configuration and durable lifecycle,
   hashes, orders/fills/journal and declared cost evidence; exclude provider keys
   and raw prompts/responses. No claim of cryptographic authenticity against DB owner.
5. Outbound Telegram plain-text notifications: failure, safety halt, budget warning,
   daily summary. Explicit bounded CLI scans selected experiment and queues events;
   delivery is a separate explicit CLI action. PostgreSQL outbox deduplicates event
   keys and durably claims before network. Unknown/crashed delivery remains unknown,
   never blindly retried. No exact-once delivery guarantee. Fixed Telegram API host,
   injected contract-test transport, deadlines/size limits, no bot controls/polling,
   no paid broadcast, no scheduler or real message in development/CI.
6. Test-first API/auth/escaping/Decimal/missing-evidence cases, real PostgreSQL
   migration, read-only integrity and notification concurrency/recovery cases.
   Playwright Python is a development-only dependency; Chromium browser tests use
   actual local API and isolated PostgreSQL with deterministic fixtures. CI installs
   Chromium and full `make check` includes browser tests. Inspect desktop/mobile,
   light/dark, keyboard and failure states. Independently review complete changes.

## Acceptance and boundaries

Run Ruff format/lint, strict mypy and complete `make check`, plus production build,
new migration round trip/schema parity, health and packaged dashboard/export smoke.
Keep existing 846 tests required. Verify exports and browser navigation do not write
trading/experiment state or call AI. Auth errors precede persistence reads; no raw
error/secret logging. Malicious source descriptions render as text. SQL/list/report
work and responses have explicit bounds and timeout. No global stale-source inference
from a healthy database; last receipt and recorded quality are distinct evidence.

Update README, configuration examples, architecture/testing/infrastructure/roadmap,
decision register and progress log with observed evidence. No success claims before
checks run. Up to three automatic repair rounds for failures/confirmed review issues.
Real Telegram smoke requires later user approval, target and local bot credentials.
M8 continuous scheduling, local multi-day smoke and deployment remain separate.


## Local acceptance — 2026-10-20

Full `make check`: PASS, 924 tests, 96.35% statement/branch coverage; Ruff and
strict mypy (123 files). Includes 8 Chromium tests with actual loopback API and
PostgreSQL, across desktop/mobile and light/dark plus negative cases. Screenshots
visually inspected; mobile wide tables scroll locally with keyboard access.
Fresh independent review PASS after the three recorded repair rounds. No separate
read-only sandbox or reviewer-run full suite is claimed.

Production rebuild, packaged assets, additive migration0009/schema parity, exact
M6 economics, repeated identical audit exports, original lifecycle replay, auth
denial and health PASS. Explicit CLI queue scanned the same checkpoint at two
different times: one pending summary reused, no real message sent. Development
Playwright/pytest packages are absent from the production image. Runtime fingerprint
`587a0ec008f302c87f8643b73ef3b853b46722945b436ac65773072351b1063f`.

Shipping/hosted CI and real Telegram delivery are NOT RUN and separately gated.
Synthetic results prove engineering behavior only, not investment performance.
