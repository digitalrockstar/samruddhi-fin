# Autonomous Factory Architecture

## Purpose
Build a creator-driven system that turns natural-language ideas into versioned product specs, dependency-aware tasks, implementation, browser test results and a live tracker. Codex is the first coding agent; the orchestration layer must remain model-agnostic.

## Subsystems
1. Creator workspace: project intake, reference attachments, specification review, feedback, decisions, pause/resume and approvals.
2. Product intelligence: creates BRD/PRD, design system, architecture, data model, acceptance criteria and open questions. Documents are versioned.
3. Task planner: creates modules, tasks, verification steps and dependency edges.
4. Orchestrator: dispatches ready tasks, owns lifecycle transitions, enforces retry/time limits, resumes interrupted work and updates persistent records.
5. Codex adapter: wraps a verified local Codex execution interface, captures actual results/exit code/events; no unverified flags or APIs.
6. Verification worker: executes deterministic checks and Playwright/Chromium journeys with screenshots.
7. Artifact store: logs, diffs, screenshots, traces and test reports linked to tasks and runs.
8. Build Console: reads persisted state and streams updates over SSE or WebSockets.

## Data flow
Creator input -> persisted change request -> impact analysis -> approval when policy requires -> updated specification/task graph -> scheduler -> isolated agent run -> deterministic verification -> evidence storage -> task state update -> live dashboard. Failures return real evidence to the agent within a bounded repair loop. Independent work may continue while a task is blocked.

## First vertical slice
- One Samruddhi Fin project record.
- One task with explicit acceptance criteria.
- One Codex invocation using a supported integration verified on the developer machine.
- One deterministic test command executed for real.
- Persist run output, timestamps, exit code and evidence reference.
- Display task/run status in a minimal Console.
- Restart and confirm persistence.
- No simulated activity.

## Proposed initial stack
Prefer TypeScript for the new factory unless environment discovery shows a clear reason not to:
- Next.js/React Build Console.
- Node.js/TypeScript orchestrator.
- SQLite for local Phase 1; PostgreSQL if/when hosted multi-user use is needed.
- Server-Sent Events for simple live updates.
- Playwright/Chromium for end-to-end verification.
These are candidates, not installed dependencies. Inspect the machine and existing Codex integration before choosing dependencies.

## Core records
projects, specification_versions, creator_inputs, decisions, modules, tasks, task_dependencies, agent_runs, task_events, verification_runs, artifacts, approvals, blockers.

Every state-changing event has ID, timestamp, actor, task/project, event type and payload. Redact secrets from output before persistence.

## Ownership
The orchestrator is authoritative for task status. Agents may report work but cannot self-certify verification. Test runner output is the source of test outcomes. The UI cannot manually turn a task green.

## Safety
- Treat legacy folders as off-limits.
- Use synthetic data in tests.
- No production deployment/destructive operations without approval.
- No credentials in prompts or stored logs.
- Limit retries and command permissions.
- Do not permit arbitrary remote shell execution through the creator interface.
