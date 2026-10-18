# 16 — Source register and verification status

**Date:** 2026-10-18. External documents and API contracts can change without warning. This register is a list of primary places to verify, **not** proof of current endpoint compatibility or legal trading eligibility.

| Source | URL / local path | Use | Verification status |
| --- | --- | --- | --- |
| Supplied third-party GRONK guide | `../references/GRONK-BOT-TEAM-GUIDE.docx` | Strategy hypotheses, claims and proposed flow | READ, performance UNVERIFIED |
| DexScreener API reference | https://docs.dexscreener.com/api/reference | Token/pair snapshots, documented rate limits | M2 synthetic contracts + public sample persisted; bounded mint pairs only |
| Solana JSON RPC docs | https://solana.com/docs/rpc | Supply and largest token accounts, chain data | M2 supply observed/persisted; largest accounts rate-limited; synthetic contracts pass |
| Polymarket developer docs | https://docs.polymarket.com/ | Market metadata, order book, history | M2 synthetic contracts pass; public smoke network_error; region rules NOT VERIFIED |
| Kalshi API docs | https://docs.kalshi.com/ | Markets, prices, order books | M2 metadata/YES-NO book sample persisted; account eligibility NOT VERIFIED |
| xAI developer docs | https://docs.x.ai/ | Grok API model access, pricing and tools | Account/model billing NOT CONFIGURED |
| OpenAI developer docs | https://platform.openai.com/docs/ | OpenAI API, usage and security | Personal API NOT CONFIGURED |
| Anthropic developer docs | https://docs.anthropic.com/ | Claude API, token billing | Personal API NOT CONFIGURED |
| Codex repository instructions | https://developers.openai.com/cookbook/examples/codex/iterating-development-workflows-with-codex | `AGENTS.md` convention and phased development | Official guide consulted |

## Pre-integration verification checklist

- [ ] Current API docs and applicable ToS/rate limits reviewed and a specific version/date recorded.
- [ ] No-auth read-only access proven where claimed; if authenticated, private least-privilege key provisioned.
- [ ] Source schema and pagination characterized with captured sanitized contract fixtures.
- [ ] Fee schedule and pricing sourced to official venue docs and recorded with date.
- [ ] Provider outages, rejected rate-limit responses and stale values tested.
- [ ] Prediction-market contract identifiers, resolution wording and market availability validated.
- [ ] No unsupported assertion that local access entails legal permission for real-money trading.

New claims should cite their evidence in docs/ADRs. Avoid relying on social media screenshots, referral links or model memory for product capabilities.


## M2 verification evidence — 2026-10-18

Official contracts were reviewed during M2 implementation: DexScreener
[token profiles/pairs](https://docs.dexscreener.com/api/reference), Solana
[getTokenSupply](https://solana.com/docs/rpc/http/gettokensupply) and
[getTokenLargestAccounts](https://solana.com/docs/rpc/http/gettokenlargestaccounts),
Polymarket [Gamma markets](https://docs.polymarket.com/api-reference/markets/list-markets)
and [CLOB book](https://docs.polymarket.com/api-reference/market-data/get-order-book),
Kalshi [market data](https://docs.kalshi.com/getting_started/quick_start_market_data)
and [fixed-point migration](https://docs.kalshi.com/getting_started/fixed_point_migration).
These references do not authorize accounts, trading, paid APIs or legal eligibility.

Explicit CLI reads used no provider credentials and persisted to the separate local
`ai_trading_lab_m2_smoke` database. Capture files stayed under `/private/tmp`, outside
Git. Observed outcomes:

| Source | Bounded attempt | Observed persisted result |
| --- | --- | --- |
| DexScreener | Wrapped SOL mint pairs, limit 1 | 1 HTTP 200 capture, 1 observation, no failure; PARTIAL because event time unknown |
| Solana public mainnet RPC | Supply + largest accounts, one mint | 2 captures, 1 supply observation; largest-account request 429 after bounded retries; partial run, rate_limited |
| Polymarket | Open market sample, limit 2 / 1 page | 0 captures/observations; failed run with network_error; no successful public compatibility claim |
| Kalshi | Open market sample, limit 2 / 1 page | 2 HTTP 200 captures, 2 outcome observations; PARTIAL because event time unknown |

DexScreener and Kalshi archives replayed offline into PostgreSQL. Repeating the
Dex replay inserted zero new captures/snapshots. Synthetic contract fixtures cover
all four sources; they are not described as captured production responses.

Limits: token-profile sampling is not exhaustive launch discovery. Largest token
accounts are not unique holders. Polymarket contract values retain explicit
`collateral_units_per_contract` with collateral identity unspecified; no USD/stablecoin
equivalence is assumed. Kalshi complementary asks are marked derived and do not
prove executable liquidity. No continuous uptime, historical completeness, fee/fill
realism, legal access or performance result was established by these samples.
