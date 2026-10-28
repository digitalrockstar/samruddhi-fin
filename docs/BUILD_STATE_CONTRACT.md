# Build State and Event Contract

## Task states
not_started, ready, in_progress, blocked, implemented, verified, failed, deferred, cancelled.

Only the orchestrator transitions task state. Each transition is validated, timestamped and persisted. implemented means a change exists; verified requires the defined acceptance checks to pass with evidence.

## Agent run states
queued, starting, running, succeeded, failed, timed_out, cancelled, needs_approval.

Persist adapter, start/end timestamps, safe invocation metadata, exit code, structured output references and redacted summary.

## Verification record
Each verification record stores run ID, task ID, test command/suite, start/end timestamps, exit code, result (passed/failed/inconclusive), output artifact and parsed test counts only if actual runner output supports those counts. Do not invent counts where output parsing is uncertain.

## Task record minimum
id, project_id, module_id, title, description, status, priority, risk, acceptance criteria, dependencies, retry budget/attempts, timestamps, linked agent runs, linked verification runs, evidence references, blocker, specification version.

## Event example
```json
{
  "event_id": "uuid",
  "project_id": "samruddhi-fin",
  "task_id": "bootstrap-proof",
  "actor": "orchestrator",
  "type": "verification.completed",
  "timestamp": "RFC3339",
  "payload": {"result": "passed", "exit_code": 0, "evidence_ref": "artifacts/test-runs/example.log"}
}
```
This is a contract example only, not evidence of a real run.

## Progress calculation
Display separately: verified tasks/required tasks, verified acceptance criteria/total, actual test pass/fail/skipped counts from valid reports, in-progress/blocked, elapsed time and estimated completion with method/uncertainty. Never use a free-form agent statement as the sole source.
