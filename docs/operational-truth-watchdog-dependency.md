# Operational Truth watchdog dependency

Status: design only; implementation intentionally stopped in Verdad
Operacional-A.

## Repository evidence

The current durable run stores are `agent_runs`, `agent_schedule_runs`, and
`pipeline_runs`. They record start, finish, and terminal status, but none has a
durable heartbeat, lease deadline, or monotonically fenced liveness record.
Deriving liveness from `started_at` would confuse long-running work with a
stalled process and would invent a cause that the runtime has not observed.

For that reason, this delivery does not add a watchdog, a synthetic alert, or a
new persistence mechanism. A schema change is a required prior dependency.

## Proposed prior dependency

Add a workspace-scoped heartbeat relation owned by the service that executes
the agent. Its minimum server-owned fields are:

- tenant and workspace UUIDs;
- agent UUID and schedule-run ID;
- run/attempt identity and a monotonically increasing fencing value;
- lease start, last heartbeat, and lease expiry timestamps sourced from
  PostgreSQL time;
- state enum limited to running, completed, failed, and abandoned;
- sanitized failure class, never a raw exception or payload;
- created/updated timestamps.

The unique identity should be `(workspace_id, schedule_run_id, attempt)` and
the active lease should be fenced so an older process cannot refresh or close a
newer attempt. The relation must use forced RLS with tenant/workspace GUCs and
must not permit an unscoped service read.

## Watchdog behavior after the dependency exists

The watchdog enumerates server-owned active workspaces, opens a fresh scoped
transaction for each workspace, and compares PostgreSQL `clock_timestamp()`
with the stored lease. It may report only these observed states:

- a scheduled run has no first heartbeat after its bounded grace period;
- a running attempt has an expired lease;
- consecutive durable attempts ended failed;
- a previously reported condition has a new live heartbeat or a terminal
  completion.

It must not infer root cause, impact, data freshness, or external-system state.
One deterministic signal key per workspace/agent/condition is upserted, and
recovery updates or resolves that same signal. Empty or partial workspace reads
produce no alert. Failures in one workspace remain isolated and observable as a
sanitized operational failure.

## Required red-first proof

The dependency PR must prove with real PostgreSQL and two workspaces:

1. current attempt heartbeats cannot update another workspace;
2. an old fenced attempt cannot revive or close a newer one;
3. retry/replay creates at most one active lease and one watchdog signal;
4. never-started, expired, repeated-failure, recovery, and normal long-running
   cases are distinguished without sleeps by database-time barriers;
5. missing heartbeat storage is fatal and creates no alert;
6. recovery resolves the existing signal instead of creating a second signal.

No watchdog code should be authorized until the schema, forced RLS, retention,
and lease timing policy are approved together.
