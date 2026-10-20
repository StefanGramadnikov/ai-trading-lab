# 02 — Requirements and acceptance conditions

**Status:** User choices marked APPROVED; numeric thresholds and vendor choices may remain PROPOSED/OPEN. **Date:** 2026-10-17.

## Functional requirements — APPROVED

| ID | Requirement | Acceptance direction |
| --- | --- | --- |
| FR-01 | Support **Solana memecoins** and **prediction markets** | Both have data adapters, normalized instruments and initial strategy candidates |
| FR-02 | Test multiple strategy families independently; include GRONK-inspired rules as a hypothesis | Strategies independently versioned; no claims copied as results |
| FR-03 | Implement **Hybrid**, **Multi-agent**, and **Rules-only** approaches | Common experiment interface and isolated portfolios |
| FR-04 | Multi-agent supports **sequential** and **collaborative** variants | Recorded message/tool trace, bounded collaboration |
| FR-05 | Model-agnostic provider adapters for OpenAI, xAI and Anthropic | Swap providers via configuration; track provider/model/version |
| FR-06 | Support **periodic/continuous** and **event-driven** AI evaluation | Same market feed; compare incremental benefit and cost |
| FR-07 | **Adaptive** trading frequency; no quota of trades | Candidate qualification drives orders; zero trades is valid |
| FR-08 | Fully autonomous **paper** trading | End-to-end unattended simulation with deterministic risk gate |
| FR-09 | Controlled self-optimization | AI may suggest/code/backtest; user approves any production deployment |
| FR-10 | Compare conservative/moderate/aggressive **paper** risk profiles | Risk policy configs and independent experiment traces |
| FR-11 | Local Mac and later VPS use the same portable application | Docker Compose, pinned versions and parity smoke tests |
| FR-12 | Web dashboard and Telegram reporting | Cost, risk, P&L, execution, errors and experiment status |
| FR-13 | Experiment lifecycle and stop/pause rules | CREATED→RUNNING→EVALUATING→PAUSED/APPROVED/REJECTED events |
| FR-14 | Performance **after all expenses** is the economic objective | Explicit metrics and allocated costs, no double counting |
| FR-15 | Local smoke 2–4 days, then initial cloud/extended paper evaluation up to 30 days | Human GO/EXTEND/STOP decision at checkpoint; no automatic live switch |
| FR-16 | Separate virtual ledgers; total future real-capital intention initially ≤€100 | Paper portfolios labeled simulated; future pilot separately approved |
| FR-17 | Document behavior, versions and experimental results | Reproducible run metadata and audit trail |

## Engineering and quality requirements — APPROVED

| ID | Requirement | Acceptance direction |
| --- | --- | --- |
| NFR-01 | **Tests every development cycle must run and pass** | Exact test and lint/type commands reported; CI green |
| NFR-02 | New work adds tests; old tests updated when legitimately required | Diff includes appropriate positive, negative and regression cases |
| NFR-03 | Git version control **from beginning**; private remote | User creates remote; check whether `git init` needed; first docs commit |
| NFR-04 | Keep code easy to maintain and modular | Clear interfaces and bounded integration complexity |
| NFR-05 | Budget-aware operation | Per-provider token/call logs and hard client-side spending ceilings |
| NFR-06 | Correctness of accounting / simulation | Deterministic money math, order lifecycle invariants and scenario tests |
| NFR-07 | Recovery safety | Durable replay, idempotency, state reconciliation after crashes |
| NFR-08 | Security | Least privilege, secrets excluded from Git/logs, opt-in live capability |
| NFR-09 | Reliable source provenance | Source/event/receipt timestamp, schema/version, stale-status |
| NFR-10 | No vendor lock-in for models or trading venues | Adapter boundaries, explicit feature capability matrix |

## Money, budget and evaluation — APPROVED context

- **Expense budget:** aim ≤€100 for an initial *experiment* and favor free components; spending is tracked separately from virtual money.
- **Future live capital:** ≤€100 initially, *potentially adjustable if justified* and approved. No funded trading account is required now.
- **Evaluation:** 2–4 local smoke days; cloud/extended paper phase up to ~30 days; decision at its end is **not an automatic GO**.
- **Economic criterion:** trading returns should exceed both execution and operating expenses. “Positive trading return” alone is insufficient.
- **User labor:** favor unattended operation with minimal manual sales/support and manageable upkeep.

## Proposed, not yet approved as exact specification

- M1 foundation tool selections are resolved in ADR-0005: Python 3.12.13, PostgreSQL, FastAPI, Compose, pytest, Ruff, mypy and GitHub Actions. M7 UI choice is resolved by DEC-037: static HTML/CSS/JavaScript served by FastAPI.
- Five-minute continuous-AI cadence: **placeholder**, not an optimized interval.
- Conservative/moderate/aggressive example exposure limits from prior conversation: **illustrations**, not binding financial policy. Calibrate from simulations and document explicit approval.
- A €5–15 monthly VPS cost and separate provider budgets: **estimates**, must be priced and reviewed before purchase.
- Which prediction-market venue is accessible, compliant and suitable for live operation: **OPEN**.

## Formal safety gates

- **M0/M1:** documentation and skeleton only; no funded account, live API request or wallet.
- **Paper trading:** credentials, if needed, must only access read-only data; execution mode defaults to `PAPER_ONLY` and fails closed when misconfigured.
- **Live consideration:** requires explicit user authorization, venue eligibility assessment, legal/tax review, execution testing, documented risk limits, secure isolated funded account, kill switch, reconciliation and deployment approval.

## Unresolved requirements

1. RESOLVED 2026-10-17: initialized local clone and origin verified; see progress log.
2. Which initial data sources are usable without authentication, and what discovery coverage they offer.
3. Whether to include unresolved 30+ day prediction-market contracts in the main evaluation or a separate longer cohort.
4. Maximum AI expense **per day**, number of concurrent experiment cells, and acceptable CPU/storage load on Mac/VPS.
5. Exact risk-limit numbers for paper and any potential live pilot; define as configuration after tests.
6. Exact legal and operational feasibility of real trading from the user's jurisdiction; never infer from website accessibility alone.
