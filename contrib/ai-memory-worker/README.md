# Dedicated memory worker preparation

This is a deployment review package, not a deployment script. No infrastructure
is created by importing the code or installing the heartbeat schedule. The
producer switch `AIMMS_MEMORY_WORKER_ENABLED` defaults to `false`.

## Intended application pair

| New application | Source worker configuration to review | Queue |
|---|---|---|
| `aimms-memory-worker` | `inventree-worker` | `ai-memory` in that environment's database |
| `aimms-dev-memory-worker` | `aimms-dev-worker` | `ai-memory` in that environment's database |

The names are the implementation plan's intended targets; this package does not
verify current Azure resources, image digests, identities or database settings.

`containerapp.overlay.yaml` specifies the shared changes for both new apps:
one replica, Single revision mode, 0.5 vCPU / 1 GiB, `invoke worker`, and
`Q_CLUSTER_NAME=ai-memory`. It is intentionally **not standalone**. Substitute
the application name and reviewed immutable image digest, then merge it into
a separately reviewed full deployment definition for each environment.

Preserve/review registry access, environment/resource IDs, unified DB secret
references, database host/port/pooling policy, signing-key continuity, mounts,
networking and managed identity. Do not copy secret values into this package.
Assign the required model/data roles to each new identity under the separate
identity rollout. Compaction now requires keyless authentication through the
shared factory; other client rails retain their existing configuration. Qualify
worker credentials before deploying this change to compaction consumers. There
is no key fallback for memory calls; rollback uses the previous image.
Memory-worker applications need no public HTTP ingress; adapt inherited probes
for a queue consumer. The default worker must continue running ingestion and
its existing schedules.

## Queue and execution contract

- `ALT_CLUSTERS['ai-memory']` uses ORM database `default`, one worker, queue limit
  20, bulk dequeue 1, default timeout 300 seconds and retry timeout + 120.
  `INVENTREE_MEMORY_TIMEOUT` supports 30–900 seconds; all producers and consumers
  must agree. Alternative broker/synchronous settings are explicitly cleared.
- The application preserves Django-Q's signing prefix (`name='InvenTree'`);
  `Q_CLUSTER_NAME` selects the alternative queue without changing its signing salt.
- The selected cluster keeps its scheduler enabled for **its own explicit-cluster
  heartbeat**. Default/null-cluster schedules remain on the default consumer.
- The new producer uses `async_task` with explicit cluster, ORM broker, timeout
  and `sync=False`. It bypasses the generic offload batching path, so admission
  is checked at actual publication. Only the existing compaction function is
  admitted; no arbitrary callable or semantic extraction is exposed.
- Admission requires a successful heartbeat from this exact cluster within ten
  minutes. Default-cluster success and unrelated tasks do not qualify readiness.
- Depth above 50 or oldest due-lock age above 600 seconds defers work. `OrmQ.lock`
  is a retry timestamp that changes on dequeue; this is **not original task age**.
  Queue queries read counts/timestamps, never task payloads. Pressure is advisory
  under concurrent producers, not an atomic hard cap.
- The execution wrapper rechecks the actual cluster, ORM configuration, restore
  hold and routing switch before entering existing compaction logic. Its
  `processed` return means the function returned, not that a new summary was written.

## Future activation sequence

Runtime tests, migrations and deployment are currently paused. The following
commands are review inputs for a later authorized qualification period:

1. Qualify configuration selection, ORM routing, pressure/heartbeat tests and
   worker timeout/retry behavior on a disposable environment.
2. Prepare and review both complete deployment definitions, identities, database
   connectivity and resource costs. Deploy a qualified image with routing **off**.
3. Preview the heartbeat schedule with `python manage.py configure_memory_worker`.
   Only `--execute` installs/updates the owned schedule; run configuration once
   per environment, serially. No schedule is auto-created by app startup.
4. Confirm `python manage.py memory_worker_status --fail-on-unready` succeeds.
   This can qualify consumer liveness while producer routing remains off. It is
   not model, database-capacity, extraction or deployment acceptance.
5. Enable the routing switch on the qualified memory consumer, then on producers
   in a reviewed cutover. Existing compaction enablement flags still apply.
   Drain or account for already queued default-cluster compaction work first.
6. Check actual queue placement, heartbeat age, delayed work, timeout/retry effects,
   resource usage and the continued availability of the ingestion consumer.

## Deferral and rollback limits

No fallback sends admitted memory work to another queue or executes it inline.
The default-off path retains the existing compaction placement. A dedicated-path
failure is logged with a fixed reason/counts and leaves the transcript backlog
intact. A later terminal turn can retry scheduling. Durable extraction claim rows,
the ten-minute recovery sweep and extraction-deferred turn stamps remain M3a work;
this batch does not promise autonomous recovery for a thread with no later turns.

Admission is not deduplication. Concurrent attempts may enqueue duplicate jobs;
the existing compactor checks its summary watermark on write, but duplicate model
work remains a qualification concern, especially under ORM retry/prefetch.

For rollback, pause producers and drain/reconcile outstanding `ai-memory` work
before disabling its consumer. Review a switch back to current default placement;
turning off the routing flag does not move existing jobs. A queued wrapper that
executes while the switch is off returns deferred without a model call. Keep the
default worker available. Pause the owned heartbeat schedule when retiring the
memory consumer. Never delete queue rows as an automatic rollback step.

The web restore hold does not stop worker processes. Restore procedures must
still fence all workers; the new wrapper/heartbeat check is an additional guard.

## Hostname guard and telemetry

The review overlay selects `AIMMS_EGRESS_MODE=enforce`. Existing environments
default to `off` until explicitly configured. `audit` records denied attempts
but permits the connection; switching mode after startup requires a restart.
`AIMMS_EGRESS_ALLOW` is an optional comma-separated replacement of the canonical
host list in `ai/core/egress.py`. Review the replacement database FQDN and every
required provider destination before activation. Wildcards match one DNS label,
never suffix lookalikes or arbitrary nested domains. The ACA `IDENTITY_ENDPOINT`
may add its configured local/private IP; IMDS is excluded.

The guard wraps Python DNS resolution and `socket.create_connection` from
`AIChatConfig.ready` (there is no separate installed `ai` AppConfig). Denials log
only `egress.blocked` and mode. This is a compensating control: direct socket/IP
connections, DNS rebinding, libpq, gRPC, native clients and subprocesses are not
contained by it. Network-level controls and the signed risk decision remain open.
The existing no-egress CI island will collect the authored guard cases.

The image and startup set `MEM0_TELEMETRY=false`; this does not admit or import
Mem0, disable other vendors' telemetry, or prove absence of vendor egress. Mem0
admission and its package-specific verification remain separate prerequisites.


## Extraction recovery and spend accounting

The default-off extraction path writes a per-thread/sequence claim after a
fresh turn. `configure_memory_worker` previews both heartbeat and recovery
schedules; `--execute` installs both on ai-memory. Code deployment alone does
not install schedules. A one-minute recovery pass republishes at most 50 due
claims and releases processing leases older than the worker timeout plus 180
seconds. The callback publishes only metadata; no model runs on the chat request.

A task handles at most five source messages / 10,000 characters and five
candidates. Source policy and consent run before provider calls. Three failed
attempts leave an explicit failed-window diagnostic and advance that bounded
window so future cumulative claims cannot retry it forever. Those gaps must be
reviewed in final qualification; they are not successful extractions.

Set `AIMMS_WORKER_DAILY_TOKEN_CAP_EXTRACTION` for the intended environment.
Its existing value `0` means unlimited. PostgreSQL advisory locking serializes
admission; the existing usage ledger reserves a conservative byte/schema/output
bound before a model call. Known usage replaces that row. Unknown outcomes retain
the estimate, including after a worker crash. `usage_report` identifies unsettled
reservation totals; they are not measured provider usage.

Model and shield clients are keyless, bounded and have no SDK retry/fallback.
No provider call, queue schedule or semantic flag has been activated by these
source changes. Embedding/rescreen workers and runtime qualification are separate.
Legacy voice sources are excluded until their actual session carries the new
memory-disclosing consent version; prior consent is never inferred or backfilled.
