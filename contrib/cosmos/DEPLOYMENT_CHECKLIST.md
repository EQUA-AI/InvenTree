# EQUA / IoT deployment checklist

## Scope and authorization

The owner requested accelerated local checklist completion and a merge commit on
`integration/equa-iot-demo`. This authorizes local code fixes, disposable database
rehearsals, verification and the local merge commit. It does not authorize a push,
registry publication, cloud deployment, shared-database migration, new Cosmos
permissions or live station activation.

- Reviewed target: `7bc90407ce30ac651d68fe2905b8c0e10459920e`.
- Reviewed IoT source: `c43f330f1ef54cce7d6d56f6aa9b1aada5724917` (45 commits).
- Original dirty `equa/customizations` checkout remains separate and untouched.
- Newer unreviewed IoT commits remain excluded.
- Polling stays opt-in: `AIMMS_COSMOS_PUMPHOUSE_ENABLED=False`.

## Historical accepted-merge code gate

- [x] No unresolved Git conflicts; additive assets migration join retained.
- [x] Historical migrations unchanged, including IoT `0011` and all 306 target files.
- [x] Django system check and assets model/migration-state check pass.
- [x] Full historical SQLite migration tests pass (4 tests).
- [x] Real disposable SQLite rewind command preview/refusal/apply/reapply pass.
- [x] Both assets-history upgrade directions pass on SQLite and PostgreSQL.
- [x] Frontend unit tests, TypeScript/production build and mimic browser tests pass.
- [x] All 39 translation catalogs preserve target translations.
- [x] Full exact-original IoT PostgreSQL database upgrades, preserving original
      records/history (861 applied records before, 956 after).
- [x] Full exact-original IoT SQLite database upgrades, preserving original
      records/history (861 applied records before, 956 after).
- [x] Cosmos default/classification/import-retry findings resolved and verified;
      clientless ingestion fails closed. Focused 91-test and broader 252-test
      SQLite checks pass (39 expected skips in the broader lane).
- [x] Registry secret-scanner false-positive resolved by a semantic alpha-rename;
      same-path and separate-path synthetic credential canaries remain detected.
- [x] Final configured hooks pass without modifying candidate files (exit 0).
- [x] Final PostgreSQL core/emulator regression suite passes: **725 tests**, exit 0.
- [x] Independent review accepts final fixes without blocking findings; all
      **156 staged paths** match the tested/reviewed candidate blobs.

The local two-parent merge commit and clean integration worktree are read back
immediately after commit. Its exact SHA, parents and blob-manifest verification
are recorded in the completion handover outside the commit's own tree.

New Cosmos sources created through a form use 300 seconds when the submitted
freshness is the old rendered 900-second default. To deliberately configure
900 seconds through a form, create the source and then edit it; existing rows
and constructor-explicit thresholds are preserved.

Existing failed runs are preserved as failed. Accelerated completion reuses prior
passing evidence for unchanged source and reruns affected seams; it does not
silently claim every broad suite passed. Target-only permission-model omissions
and unrelated target migration drift must remain explicitly documented.

## Non-demo consolidation gate

Synthetic runtime/tooling is retired on `equa/customizations`; the accepted
merge and immutable historical migrations remain ancestors. The additive
state-only retirement migration retains database data and foreign keys. The
historical checks above describe the accepted merge, not automatic qualification
of the consolidated tree. Its exact verification and preservation evidence is
recorded in the consolidation handover. No production branch, push or deployment
is part of that local update.

## Release preparation and rollout: flag off

These are deployment operations, not consequences of committing code.

- [ ] Approve publication/rollout of the exact merge commit and intended environment.
- [ ] Build the repository `contrib/container/Dockerfile` production target from
      committed, allowlisted inputs with the full commit as `commit_hash`.
- [ ] Inspect source/image parity, matching frontend/backend build identity and
      installed `azure-cosmos` / `azure-identity`; record immutable image digest.
- [ ] Capture current web/worker commands, identities, mounts, queues, flags,
      image digests and baseline health without recording secret values.
- [ ] Confirm backup and recovery checkpoint for the actual PostgreSQL database.
- [ ] Assign one migration owner/executor, `equa-iot-migrate`; preview the pending
      migration plan, quiesce incompatible web/worker consumers, then run only
      `python manage.py migrate --noinput` through that approved executor.
- [ ] Set `INVENTREE_AUTO_UPDATE=False` on web and worker, with polling still false.
- [ ] Deploy web and worker to the same immutable digest; preserve ASGI, port,
      worker command, queues, mount and secret references.
- [ ] Verify schema, authenticated release checks, machine/location/chat/maintenance
      flows, worker heartbeat and representative task completion before promotion.

Use [AIMMS release checks](../container/AIMMS-release-checks.md) for authenticated
candidate and serving-host checks. A public health response is not release proof.
Do not use `rewind_migrations` for deployment recovery: forward-fix only; backup
restore requires its own approved rehearsal.

## Separate live-IoT activation gate

- [ ] Obtain prior client approval for external integration.
- [ ] Approve telemetry provenance/sensitivity and exact source configuration.
- [ ] Confirm effective read-only Cosmos identity/network access for both web and
      worker; the recorded web access check does not establish worker access.
- [ ] Approve client/station crosswalk, catalogue, dictionary, units, thresholds
      and layout; preserve cursor positions and existing leases.
- [ ] Strict readiness and bounded read-only query/producer checks pass.
- [ ] Authorize station activation and, separately, the server/worker polling flag.

The non-production account `epconchatcosmos9d6b` is not production deployment
approval or evidence that every document is synthetic/sanitized. No plant-data
write probe is permitted. Keep unavailable/stale/unreviewed values unavailable.
