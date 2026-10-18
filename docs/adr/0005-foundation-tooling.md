# ADR-0005 — M1 foundation tooling

- **Status:** APPROVED plan; exact pins verified during implementation
- **Date:** 2026-10-17
- **Authorization:** User approved the M1 plan with “yes, continue”.

## Decision

Use Python 3.12.13, uv 0.11.7, a src-layout package and committed `uv.lock`.
Use FastAPI + Pydantic Settings, async SQLAlchemy 2 + psycopg 3, Alembic,
PostgreSQL 17.9, pytest/pytest-asyncio/pytest-cov, Ruff, strict mypy,
pre-commit and GitHub Actions. Pin Hatchling 1.32.4 for package builds.
Runtime/test dependencies resolve to exact versions and artifact hashes in the lockfile.
Use httpx2 for TestClient, per the installed Starlette version's supported interface
and [official documentation](https://starlette.dev/testclient/).

Compose starts only API and database; no worker/dashboard until their milestones.
API startup accepts only PAPER_ONLY. Liveness and database readiness are distinct;
readiness never claims trading readiness. Baseline migration records version history
without creating future market, portfolio or trading tables.

## Consequences

One `make check` command runs the required quality gates locally and in CI.
Integration tests require a dedicated disposable PostgreSQL database and never skip
silently. Local image builds and tests do not establish hosted CI success.
Python 3.12 requires installation because the observed Mac default was 3.9.6;
uv can install it without changing the system Python. No dependency uses paid APIs.
Container tags are version-pinned but registry tags may be republished; digest pinning
can be added during deployment hardening. No architectural decisions are superseded.

## References

- [uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/)
- [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/)
- [M1 executable gate](../08-testing-standards.md)
