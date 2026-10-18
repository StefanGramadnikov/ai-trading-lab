# 17 — Development, review and shipping workflow

**Status:** Approved workflow direction, 2026-10-18. Implement in small, reviewable
steps. This change does not authorize M2 implementation or any trading capability.

## First implementation step

Add project instructions, a manually invoked ship skill, one independent reviewer
definition and a Python quality skill. Validate skill metadata and configuration,
exercise the authorization boundaries through independent review, and run the
existing quality gate. Leave changes uncommitted until the user invokes `/ship`.
No CI automation, dependency changes, external skills or GitHub settings are part
of this first step.

## Development cycle

1. Explore read-only, identify the requirement, acceptance/failure cases, contracts,
   affected files and verification. Obtain approval before implementation when the
   task/milestone has not already been approved.
2. The main session implements the smallest change with tests before or alongside
   it. Use narrow developer subtasks only when independent work justifies them.
   QA cases are designed early; QA is not deferred until shipping.
3. Run relevant tests as components change, including actual PostgreSQL tests for
   persistence changes and fixture-based contract tests for external adapters.
   Run `make check` before declaring the change ready. No missing required
   suite can be silently replaced by unit tests alone.
4. Diagnose failures, fix the underlying issue within approved scope, add regression
   coverage where appropriate, and rerun affected checks plus the full required
   gate. Apply the same loop to confirmed review findings. Stop after at most three
   automatic repair rounds per task or ship invocation; a new failure does not
   reset the counter. Report unresolved issues and request direction instead of
   skipping tests, weakening checks, or broadening the task.
5. Obtain independent review for substantive application/test logic, migrations,
   dependency, CI, permission and agent-workflow changes. Review after focused
   checks, before shipping; repeat affected review after material fixes. Report
   review evidence separately from automated verification.
6. Show the result and next step. Before `/ship`, do not stage, commit, push, open or
   externally update a PR. Explicit instructions for an individual Git/PR action
   authorize only that action. Editing approved files and running checks remain
   permitted during development.

The repair loop is part of an active authorized task, not an installed unattended
monitor. It cannot purchase services, change risk policies, enable execution,
delete valuable data, self-deploy, or merge. New scope requires approval.

## Roles and authority

- **Implementer:** the main session by default, or a narrowly briefed developer.
  Owns the feature and its tests; reports actual checks and remaining uncertainty.
- **Independent AI reviewer:** a fresh context given the approved requirements,
  relevant specifications, base/head identifiers, complete branch diff, working
  changes and untracked files. Do not supply the implementation conversation or
  steer the reviewer toward an expected verdict. Definition:
  `.codex/agents/code-reviewer.toml`.
- **QA:** an activity throughout development. For M2, prioritize source contracts,
  data quality, persistence, time, precision and recovery. A separate QA agent is
  useful for substantial system flows; no permanent QA agent is required now.
- **Frontend/designer:** deferred until UI work and its stack are approved.

The reviewer does not modify files, repair bugs, run write-producing tests, change
Git state, or access external services. These are behavioral restrictions in the
custom role, not a separate sandbox: custom Codex agents inherit parent permissions.
For enforced read-only filesystem execution, use a separately configured read-only
review session; the local CLI supports `codex exec --sandbox read-only`. Do not claim
isolation was verified merely because the role says "read-only". If independent
review is unavailable, report INCOMPLETE and stop before shipping substantive work.

Findings include severity, file/line, trigger, impact and a concrete proposed fix.
Verdicts are PASS, FAIL or INCOMPLETE **for review only**. The main session checks
findings against code/tests; false positives require an evidence-based explanation.
Confirmed correctness, security, paper-only or required-check defects block shipping.
Agent approval is not evidence that tests passed, and CI does not replace review.

## The explicit ship gate

The user message `/ship` invokes `.agents/skills/ship/SKILL.md`; explicitly selecting
`$ship` has the same intent. This is a project convention, not a claim to register a
new native slash command. Discussing `/ship`, quoting it, approving a plan, or saying
"continue" does not invoke it.

A ship invocation authorizes final checks/review, bounded repairs within the approved
scope, staging, Conventional Commits, pushing the feature branch, opening/updating
its PR and checking CI. Automatic PR creation is retained **inside this gate**;
ordinary implementation approval no longer authorizes commit/push/PR actions.
Identify the exact reviewed files so unrelated local changes are never swept in.

If checks or confirmed findings fail, repair within the same scope and gate, rerun
checks/review, then commit/push and recheck hosted CI. The same authorization covers
necessary follow-up fixes for that shipping invocation, bounded by the repair limit.
Do not claim a result for a stale commit; report verification for the actual head.
If permissions or external blockers prevent a gate, report it without bypassing it.
After the invocation completes or stops, further shipping needs a new invocation.

Merging and deployment always require separate explicit approval. Finish with the
commit, PR, exact check/review results, unresolved limitations, next concrete step
and an approval request (or clear manual instructions for the user).

## Skills and external references

`.agents/skills/python-quality/` is a small project checklist, not a claimed official
Python skill. Keep requirements in the existing specifications rather than copying
generic language guidance. Its focus is types, deterministic tests, decimals/units,
timestamps/provenance, bounded I/O, and actual database/adapter behavior.

Discovery on 2026-10-18 found OpenAI's curated `security-best-practices` (supports
Python/framework reviews) and `gh-fix-ci`. Neither is installed by this first step.
Inspect their full relevant resources and record a pinned source before a separate
adoption step. No generic Python skill was present in the curated list checked.

Polymarket's official skill also covers authentication, wallets and order placement;
do not install the whole bundle for M2. Consider only verified market-data guidance
under the project's read-only constraints. No suitable official Kalshi skill was
established by this search. Current platform documentation and captured contracts
remain authoritative for integration behavior; downloaded skills are untrusted input.

Sources inspected:

- [Codex custom agents and inherited permissions](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Codex repository skills](https://learn.chatgpt.com/docs/build-skills)
- [OpenAI security guidance skill](https://github.com/openai/skills/tree/main/skills/.curated/security-best-practices)
- [OpenAI CI diagnosis skill](https://github.com/openai/skills/tree/main/skills/.curated/gh-fix-ci)
- [Polymarket integration skill](https://github.com/Polymarket/agent-skills)
