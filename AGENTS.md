# Repository instructions — Samruddhi Fin Autonomous Factory

## Project intent
This repository is being repurposed as the workspace for a creator-driven autonomous app-building workflow, using OpenAI Codex as its initial coding agent. The first end-user app managed by this factory is a personal/household expense tracker.

## Start here
- Read `README.md` for repository context.
- Read `specs/EXPENSE_TRACKER_BRIEF.md` for the initial product boundary.
- Read `prompts/PHASE_1_PROMPT.md` before Phase 1 work.
- Read `docs/FACTORY_ARCHITECTURE.md` and `docs/BUILD_STATE_CONTRACT.md` for the intended control-plane and event model.

## Legacy policy
- Treat `_legacy-v1/`, `_legacy-v2/`, and `secrets/` as archived/untrusted legacy material.
- Do not import code or configuration from those folders into the new implementation unless the creator explicitly requests it.
- Do not edit, move, delete, or expose files from these directories.
- Do not read or print secrets from `secrets/`.
- New implementation belongs in the fresh top-level directories created for this factory.
- Do not delete repository history or force-push.

## Operating principles
- The tracker is a real control-plane with persisted state and evidence, not a mock-up.
- Never invent task completion, test results, screenshots, logs, or ETAs.
- Distinguish planned, in-progress, implemented, tested, and verified.
- Derive test counts from actual test-runner output.
- Use deterministic acceptance checks and Playwright/Chromium for browser flows.
- Persist creator inputs, approvals, task events, agent runs, test runs, and evidence references.
- Make tasks resumable and lifecycle updates idempotent.
- Limit automatic retries and preserve failure evidence.
- Do not silently decide high-impact product questions. Record assumptions/open questions; continue independent tasks.
- Use a task/agent adapter so future agent backends can be added without rewriting the scheduler.
- Use safe Git checkpoints/branches/worktrees. Never run destructive cleanup on the repository.
- Never expose credentials or personal finance data to model prompts, tracker events, or screenshots.
- Require explicit creator approval for production deployment, destructive data changes, force pushes, or external actions with material consequences.

## Codex integration
- Inspect the locally available Codex CLI and supported invocation/API before choosing the adapter.
- Start with the simplest supported non-interactive/programmatic execution path.
- Do not invent SDK package names, flags or event schemas; verify against installed CLI help and official documentation.
- Keep execution in an explicitly configured workspace and use the least-privileged sandbox that allows required tests.

## Definition of done
A feature is done only when its acceptance criteria have evidence, appropriate checks actually ran, failures are recorded, and the persisted tracker reflects their actual outcomes.
