# Phase 1 — Prove One Real Codex Execution Loop

Execute this phase only. Do not build the complete tracker or factory.

## Goal
On the current Windows workstation, prove a complete slice: persist a task, invoke local Codex through a supported and verified integration, execute a real deterministic test command, persist the actual exit code/output, display the run in a minimal Build Console, restart the controller and verify persistence.

## Steps
1. Inspect available runtimes and Codex CLI help. Do not read, print, or inspect anything under `secrets/`. Do not inspect or alter either legacy tree.
2. Verify the supported Codex invocation/API available on this machine. Do not assume flags, event schemas or SDK packages. If there is no supported local execution route, record a blocker and stop that integration—not simulate it.
3. Choose the smallest viable architecture based on installed tooling.
4. Build the minimum persistent task, run and event store.
5. Implement a narrow Codex adapter with typed inputs/outputs, safe argument passing and explicit working directory.
6. Run one deterministic test command for real.
7. Persist timestamps, command/test identifier, exit code, redacted output artifact and test counts only when reliably parsed.
8. Display persisted run/task state in a minimal console, refreshed by a real event stream or polling.
9. Add tests for state transitions, persistence and output parsing.
10. Run unit tests and, if dependencies permit, a browser smoke test.
11. Restart app/controller and prove state remains.
12. Report exactly what ran, actual outcomes, artifact paths, blockers and next phase.

## Safety and repository boundaries
- Work only at repository root and new factory directories.
- Do not read, import, modify, move, delete or expose `_legacy-v1/`, `_legacy-v2/`, or `secrets/`.
- Do not commit to main directly; use `codex/autonomous-factory-bootstrap` or a local feature branch from it.
- No destructive operation, force push or deployment.
- A key blocked criterion means Phase 1 is not complete.
