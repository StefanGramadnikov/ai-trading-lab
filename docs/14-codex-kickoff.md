> Historical M0 import/kickoff instructions. M1 was authorized on 2026-10-17;
> use README and the progress log for the current verified state.

# 14 — Codex kickoff and Git import instructions

**M0 documentation only.** Do not run these as if application implementation were already approved. User reported creating a private repo; inspect its existing files first.

## Import M0 documentation

1. Obtain the package archive and unzip locally.
2. In your private repository clone/folder, compare the extracted files to any existing files **before copying or merging**. Preserve any current `README.md`, `.gitignore`, etc., by reviewing differences rather than overwriting blindly.
3. Inspect the repo state:

```bash
cd /absolute/path/to/your/ai-trading-lab
git status --short
git rev-parse --is-inside-work-tree
git remote -v
```

4. If and only if **Git is not initialized** in this intended directory, run `git init`, then follow GitHub's instructions to add the private remote. If a remote exists, reuse it.
5. After review, stage documentation, inspect the staged diff and create the first docs commit:

```bash
git add README.md AGENTS.md .gitignore docs/ references/
git diff --cached --check
git diff --cached --stat
git commit -m "docs: record approved AI Trading Lab architecture and guardrails"
```

6. Push to the **private** remote using your existing branch naming and authentication setup. Do not print or paste secrets.

## First Codex prompt — only after M0 review and M1 approval

```text
This is the private AI Trading Lab repository. First read AGENTS.md and the
requirements, architecture, testing standards, roadmap, decisions and progress
files linked by README.md. Do a READ-ONLY discovery of the Git state,
working tree, current toolchain and existing files. Report any conflicts.

Our explicitly authorized scope is M1 FOUNDATION ONLY, not implementation
of live trading, strategies, or market orders. Do not configure any live
exchange account, trading wallet or paid API credentials.

Propose a small execution plan and then implement the minimal Python
foundation per docs/10-implementation-roadmap.md: pinned dependencies,
FastAPI liveness endpoint, PostgreSQL + Docker Compose, DB migrations,
pytest unit/integration tests, Ruff, one pinned static type checker,
pre-commit, and GitHub Actions. Add .env.example with placeholders only.

For every change, write/update tests, run formatting/lint/type/tests,
fix failures, then report EXACT commands and results. Do not claim success
for checks not run. Update docs/13-progress.md and any changed technical
specifications. Work in a safe feature branch and do not push or merge
without confirmation.

Begin with repository inspection and your proposed M1 steps; wait for
approval before any destructive change or paid service access.
```

## Prompt for resumed session if context is lost

```text
We are continuing AI Trading Lab. Read AGENTS.md and docs/13-progress.md
first, then docs/12-decisions.md and the linked requirements and roadmap.
State the last actually verified milestone and the next safe action. Do not
assume uncommitted work was completed. No real-money trading is authorized.
```

## What *not* to ask Codex initially

- “Build the entire profitable bot end-to-end.”
- “Make Grok trade from my funded wallet.”
- “Subscribe to every model and continuously call them.”
- “Ignore failing tests and ship quickly.”
