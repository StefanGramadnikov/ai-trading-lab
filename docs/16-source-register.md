# 16 — Source register and verification status

**Date:** 2026-10-17. External documents and API contracts can change without warning. This register is a list of primary places to verify, **not** proof of current endpoint compatibility or legal trading eligibility.

| Source | URL / local path | Use | Verification status |
| --- | --- | --- | --- |
| Supplied third-party GRONK guide | `../references/GRONK-BOT-TEAM-GUIDE.docx` | Strategy hypotheses, claims and proposed flow | READ, performance UNVERIFIED |
| DexScreener API reference | https://docs.dexscreener.com/api/reference | Token/pair snapshots, documented rate limits | Official reference identified; integration NOT TESTED |
| Solana JSON RPC docs | https://solana.com/docs/rpc | Supply and largest token accounts, chain data | Integration NOT TESTED |
| Polymarket developer docs | https://docs.polymarket.com/ | Market metadata, order book, history | Integration/region rules NOT TESTED |
| Kalshi API docs | https://docs.kalshi.com/ | Markets, prices, order books | Integration and account eligibility NOT TESTED |
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
