---
name: ship
description: Finalize approved AI Trading Lab work with checks, independent review, bounded fixes, commit, push and pull request. Only run when the user explicitly invokes /ship or selects this skill; discussing the gate is not invocation. Never merge or deploy.
---

Read `AGENTS.md`, `docs/08-testing-standards.md` and
`docs/17-development-workflow.md` relative to the repository root. They define
required gates and current authorization. This skill is manually invoked only.

1. Verify invocation applies to the current approved work. Identify the feature
   branch, base/head, exact files and unrelated changes. If on main, create an
   appropriate feature/fix/experiment branch before staging. Plan approval,
   discussion or a quoted `/ship` is not shipping permission.
2. Check acceptance cases and regression coverage. Run the full repository gate
   (`make check`) for the reviewed work, plus changed migration/container checks
   where relevant. Add structural/link checks for documentation and workflow
   changes; they do not replace the required gate. Report every unrun check.
3. Independently review substantive changes with the code-reviewer role in a
   fresh context. Supply the approved contract, safety specs, complete diff and
   verification evidence, not the implementation discussion or expected verdict.
   Pure wording corrections need no separate reviewer. Missing independent review
   of substantive work is INCOMPLETE and blocks shipping.
4. Verify findings. Fix confirmed defects within scope, add regression tests where
   appropriate, rerun affected checks and the full required gate, and repeat
   affected review after material fixes. Stop after three automatic repair rounds
   total for this invocation; new failures do not reset the counter. Never weaken
   tests or change safety policy to force a pass.
5. When required checks and confirmed blocking findings are clear, inspect the
   intended files for unintended changes and secrets. Stage only reviewed files,
   inspect the exact staged diff again, run `git diff --cached --check`, and make
   a Conventional Commit with actual verification evidence. Do not claim a
   dedicated scanner ran unless it exists and actually ran. No automatic
   dependency upgrades or unapproved tools.
6. Push and open/update the branch PR with final scope, checks, review and limits.
   Attach created PRs using the app artifact tool where available. Inspect CI for
   the actual pushed head. Within this invocation, fix relevant CI failures using
   the same bounded loop; re-review material fixes before follow-up commits.
   If blocked, report actual state. Never force-push, merge, deploy or enable
   paid/live capabilities.
7. Finish with commit/PR, actual check/review results, unresolved limitations and
   the next concrete step with approval request or precise manual instructions.
   Ship authorization ends when this invocation completes or stops.

Until dedicated security scans are approved/configured, report their absence;
do not silently add tooling or call manual inspection a security audit. Preserve
the paper-only boundaries throughout.
