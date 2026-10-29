# Dashboard design, iteration 1

File: `docs/design/dashboard-v1.html` (open by double-click; no network requests; all numbers are SAMPLE DATA and labelled so on every screen). Not yet visually checked in a browser by the designer role (no commands allowed); the owner's first look may surface layout bugs.

How to use the mock: the "Preview state" selector (top right) switches the whole dashboard into any abnormal state; the **States** tab shows all of them side by side. The on/off switches work (with the confirm step and a required reason) and write to the on-screen audit trail; nothing is saved.

## Changelog
- v1: first iteration.

## Screens

### 1. Overview
- **Question:** Is it running, who is ahead, did anything go wrong, what has the AI cost?
- **Contents:** PAPER MONEY ONLY banner; status strip (loop, last/next cycle, Kraken freshness, AI spend, run length); generated verdict sentence; three strategy cards (equity, P&L in EUR and %, open positions, fees, last decision with reason, risk state, switch with wind-down confirm); one comparison chart (benchmark dashed, rules solid, AI dotted; 6 h / 24 h / 3 days / All; hover or arrow keys to read values); "Needs your attention" list with an empty state.
- **Data needed:**
  - Per-strategy equity points per cycle, cash, positions with cost basis, fees paid: EXISTS (`live_equity`, `status.py`).
  - Last decision and reason per strategy: EXISTS (`live_decisions`).
  - Switch state and audit: EXISTS (`live_strategies.enabled`, `live_strategy_audit`).
  - Age of last cycle: EXISTS (what `atl-live health` uses). "Next cycle" can be computed from the 15-minute slot.
  - Risk state per strategy (daily-loss pause, drawdown latch): EXISTS in the loop state, but must be confirmed to be exposed read-only; may need a small query.
  - Verdict sentence: derived from equity, no new data.
  - Attention list: derived from the above; **needs new work**: one small function that turns state into the list of items.
  - Position mark-to-market per position (unrealised P&L per line): **needs new work** (equity is liquidation value in total; per-position value must be derived from the latest book/price).
  - Switching from the page: **needs new work** (a write path, authentication, and a deliberate decision on exposing it; today only the CLI can change it). The mock shows it because the owner asked for it.

### 2. Decisions and trades
- **Question:** What did each strategy do and why? Why did nothing happen?
- **Contents:** filters (strategy, pair, action, status), table on desktop / cards on phone, plain-language reason per row, AI rows expand to show the model's answer, tokens, cost, call id.
- **Data needed:** decisions with action, status, reason, intent: EXISTS. AI `detail` (call id, model, answer, usage, estimated cost): EXISTS. **Needs new work:** a plain-language reason text per reason code (`ai_abstain`, `ai_unavailable_*`, risk gates and so on) as a lookup table; a paged query with filters (about 600 rows a day, so paging is required). The model's free-text reason must be shown as inert text and never fed back into a prompt.

### 3. AI spend and safety
- **Question:** Is the AI within budget and behaving? What limits protect me?
- **Contents:** budget used vs USD 10 with a meter; calls today; failure-breaker status; table of every call; the DEC-043 limits read-only, with a glossary.
- **Data needed:** spend, call count, unknown-outcome count: EXISTS (budget guard / flow ledger; needs a read-only query). Per-call rows: EXISTS in decision `detail`. Limits: EXISTS in `live/config.py`; the page must read them from the running configuration so it can never disagree with the code. **Needs new work:** "when the breaker clears" (not shown in the mock because it is not known whether the breaker uses a rolling 24 h window or a fixed one; see question 5).

### 4. System
- **Question:** Is the machine healthy, is the data fresh, is my backup recent, who changed what?
- **Contents:** loop health, first cycle time, deployed commit; backups; Kraken freshness per pair; on/off audit trail.
- **Data needed:** loop age and audit trail: EXISTS. Per-pair data age: EXISTS (candle and capture timestamps; needs a query). Deployed commit: EXISTS as `/opt/atl/DEPLOYED_COMMIT` but **the container cannot see it today**; needs it passed in as an environment variable at deploy. **Backup age: needs new work** (backups live on the host and are pulled to the Mac by hand; the app has no knowledge of them; either the backup script writes a small status file the app can read, or this panel is dropped). Restore-test result: same.

### 5. States gallery
- **Question:** What will it look like when something is wrong, and what should I do?
- **Contents:** loop stalled, Kraken unreachable, AI paused (budget), AI paused (breaker), strategy switched off, first hours, daily-loss pause, drawdown latch. Each has an icon, a word and a sentence, not just a colour. Red means "you may need to act"; amber means "the safety design is working, informational"; blue means "you chose this or it is just starting".
- **Data needed:** all derivable from the data above. Same new-work items: risk-state exposure, the attention function.

## Design decisions
1. Status first: PAPER MONEY ONLY and SAMPLE DATA bars are permanent; one status strip answers the "is it running" question.
2. A generated sentence leads the page, with the fee-inclusive wording and a reminder that the benchmark is the yardstick.
3. Benchmark is distinct by dash style, not only colour; all lines are labelled directly at their ends.
4. Amber for the safety cap and breaker (working as designed), red only for "may need you".
5. Switch confirm text states the wind-down rule explicitly and requires a reason, matching the CLI.
6. Phone: bottom navigation, cards instead of tables for decisions; desktop: top tabs.
7. Light and dark via system preference with a manual toggle; chart is keyboard-readable (arrow keys, Home/End).

## Open questions for the owner
1. **Switching from the page.** Do you want the switches to work from the dashboard (needs a login and a write path) or to be display-only with the CLI command shown? This is the largest safety decision on the page.
2. **How do you want to see it?** Only on your Mac through an SSH tunnel (safest, nothing exposed) or reachable from your phone at a password-protected address? This changes what we can build.
3. **What is "winning"?** The verdict compares profit against the benchmark after fees. Should it also say something like "not yet significant" for small gaps (for example under 1 EUR), so a lucky day is not read as a result?
4. **Alerts vs page.** Which items in the attention list should also reach you on Telegram (slice 7) and which are fine to see only when you open the page?
5. **AI breaker and budget wording.** When the AI pauses, do you want the page to say when it will resume (needs the breaker's exact rule confirmed), and do you want a warning earlier, say at 80% of the USD 10, before it stops?
