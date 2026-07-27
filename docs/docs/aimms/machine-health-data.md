---
title: Machine health data — ingestion, connectors and retention
---

# Machine health data

The Health blade on a machine page shows live industrial telemetry: current
signal values, open anomalies, and the immutable snapshots that every
preliminary result cites. This page records where that data comes from, how it
gets in, and how long we keep it.

## How data gets in

**Signed webhook.** The one generic ingestion path we ship. A gateway POSTs a
batch of readings and alarms to
`/api/machine-health/ingest/<source_id>/`, and the request is authenticated by
the delivery itself rather than by a user session — the caller is a machine, not
a person. A delivery is accepted only if all of the following hold:

| Check | Rule |
|---|---|
| Signature | HMAC-SHA256 over the exact raw body, compared in constant time |
| Freshness | Timestamp within ±300 s of the server clock |
| Replay | Delivery id not seen before (remembered for twice the window) |
| Size | Body under 1 MB, checked before any parsing |

The shared secret is resolved through the source's `secret_ref` from deployment
configuration (`AIMMS_HEALTH_WEBHOOK_SECRETS`). It is never stored on the source
row and never returned by an API. A source with no resolvable secret cannot
ingest at all: the path fails closed rather than accepting unsigned data.

This is deliberately the *basic* option. It fits any platform that can push
JSON and sign it, which is the assumption we can make before we know what a
given site runs.

## Connectors

There are no bundled connectors, and that is a decision rather than a gap.

Every plant runs a different stack — SCADA, PLC, DCS, MES, BAS/BMS, EMS, IIoT
platforms, historians — and the useful integration is the one that speaks to
*that* customer's existing system, with their tag naming, their polling limits,
and their network boundary. We develop the connector for the system a customer
already has, rather than shipping adapters against products they may not run.

`HealthSource.source_type` records which family a source belongs to and
`connector_type` names the adapter that serves it, so a purpose-built connector
is a registered adapter alongside the webhook rather than a special case.

Two properties hold for any connector we add:

* **Reads stay federated and bounded.** Trend history is read from the source
  system on demand within an explicit window and sample cap; we do not mirror a
  historian into this database.
* **Detection stays deterministic.** A connector may deliver readings and
  relay alarms the source declared. It cannot infer an anomaly of its own —
  every anomaly traces to a configured threshold or to the source system, never
  to a model. See [source authority](#source-authority) below.

## Snapshot retention

Snapshots are rows in a database table (`HealthEvidenceSnapshot`), not files and
not a cache. **They are kept for as long as the client remains a customer.**

The reason is what a snapshot *is*. It is the citation behind a preliminary
result and behind an approved repair scope: the reading, its quality, its
window, and the fact that it was already stale when captured. A snapshot never
changes after creation — an amendment is a new snapshot — so what a technician
saw when they agreed to a repair, and what an approver saw when they authorised
it, stays reconstructable afterwards.

That reconstructability is the retention requirement. In practice:

* Deleting a source or resolving an anomaly does **not** remove the snapshots
  taken under it; the foreign keys null out and the evidence remains.
* A snapshot referenced by a repair packet is preserved regardless of the state
  of the live telemetry it came from.
* Retention ends with the customer relationship, not with the work order,
  the anomaly, or the equipment.

Nothing in the product expires snapshots on a timer. If a deployment needs one —
a jurisdiction with a data-minimisation obligation, say — that is a policy
decision to be implemented explicitly, and it has to reckon with the repair
records that cite the rows it would remove.

## Source authority

When the source system declares its own alarm, whether the machine is outside
its limits is that system's determination: the boundaries are configured in the
hub the data comes from, and that hub owns the asset. The preliminary report
says so plainly — it names the source, keeps a non-zero confidence when our own
mirror of the signal is missing or stale, and points a technician at the hub for
the limits themselves.

That authority is scoped to the report. It changes how the condition is
described and how much the result hedges. It does not let an alarm start work,
satisfy a safety gate, or count as a verified diagnosis: only a person can turn
preliminary results into a diagnosis.

A condition we inferred from a threshold configured here is reported as
`derived` instead, and an alarm whose source record has been deleted degrades to
`derived` too — an unattributable claim of authority is worth less than an
honest inference.
