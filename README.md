# Samruddhi Fin — Autonomous App Factory

This repository is being set up as a fresh Codex-powered, creator-driven autonomous development factory. Its first managed product is the Samruddhi Fin household expense tracker.

## Repository boundaries
`_legacy-v1/`, `_legacy-v2/` and `secrets/` are archived and off-limits. Do not read or print secrets, import legacy code/config, or edit/move/delete the archived files. Do not rewrite repository history or force-push.

## Local workspace
`X:\Code\samruddhi-fin`

## Start with Codex
Read `AGENTS.md`, then:
1. `docs/FACTORY_ARCHITECTURE.md`
2. `docs/BUILD_STATE_CONTRACT.md`
3. `specs/EXPENSE_TRACKER_BRIEF.md`
4. `prompts/PHASE_1_PROMPT.md`

Phase 1 proves one real vertical slice: a task is persisted, Codex is invoked through a verified supported integration, a real test executes, results are saved, and a minimal Build Console displays the results and survives restart.

## Planned fresh directories
- `apps/build-console/`: creator console and live tracker.
- `apps/expense-tracker/`: target product.
- `services/orchestrator/`: scheduler and Codex adapter.
- `specs/`: generated and approved project specifications.
- `docs/`: architecture, contracts and operating policy.
- `prompts/`: bounded implementation-phase prompts.
- `build/`: planning examples; runtime database is authoritative.
- `artifacts/`: logs, screenshots, test reports; do not commit private data.

## Status
This branch contains bootstrap documentation only. No local Codex invocation, app test or browser test has run as part of this GitHub setup.
