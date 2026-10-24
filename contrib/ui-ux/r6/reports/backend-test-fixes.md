# Backend test fixes — 24 October 2026

Status: backend conclusion verification complete; UI verified for local commit.
The upstream migration policy remains an owner decision. No push, issue, PR,
Azure change, deployment acceptance or parent R6 acceptance is implied.

## Revision and worktree

Branch: `claude/clever-pasteur-c48da4`. The seven backend commits are based on
`c447e15d1` and end at `b2d690bc5`:

| Commit | Change |
| --- | --- |
| `d1f2dde15` | Carry replaced migration model state in the legacy checkpoint |
| `804476fa5` | Correct chat deletion receipts, retention last-run storage and voice cleanup |
| `4dcd1932c` | Add disposable PostgreSQL backend test execution |
| `deb36c536` | Cascade PostgreSQL test flushes into retained compatibility tables |
| `1cf56fbcd` | Keep ingestion checkpoint access within the admin ruleset |
| `f24958cb0` | Add backend test environment preparation options |
| `b2d690bc5` | Update stale fork expectations, environment profiles and blocked rewind handling |

The follow-up checkout, `.claude/worktrees/charming-villani-77eeca`, was clean
and already at `b2d690bc5` when this conclusion started. No destructive reset
was needed. Fresh backend checks run there, independently of the uncommitted
pump warning UI in the implementation checkout. The main `inventTree-aniket`
checkout remains at `c447e15d1`.

Conclusion follow-up `ce34a9e3d` adds only the five backend test-fixture
corrections described below: production timezone profiles and a valid separate
PostgreSQL observer connection. Its pre-commit hooks passed without bypasses.

## Product fixes

- Thread deletion and account erasure no longer mistake a thread identifier for
  an owner-purge reference and report a false incomplete result.
- Retention receipts compact oversized reports before writing the setting's
  2,000-character value field, avoiding PostgreSQL rejection.
- Voice cleanup deletes sessions independently, preserving a protected session
  without preventing deletion of the thread's other eligible sessions.
- Ingestion checkpoints are removed from the ordinary ruleset ignore list and
  remain admin-only. This preserves the threat model's trusted-administrator
  boundary for industrial telemetry configuration; it does not authorize OT
  control writes or grant operators access to connection configuration.

Security context: [security policy](../../../../docs/docs/SECURITY.md),
[root reporting policy](../../../../SECURITY.md) and
[threat model](../../../../docs/docs/concepts/threat_model.md). This is a local
engineering record, not an external vulnerability disclosure.

The migration-state repair carries the replaced migrations' model state and
aligns existing field declarations. Retained evidence at
`data/devscratch/backend-fixes/drift/after-sqlite-migrated-check.log` reports
`No changes detected`. No new migration file was introduced by these seven
commits. There is no new migration to apply on Azure; deploying corrected code
is separate from applying migrations and has not been performed here.

The flush override requests cascade only when `settings.TESTING` is true and
the selected connection is PostgreSQL. Normal management-command behavior is
unchanged outside that combination.

## Historical suite evidence

Evidence below lives in the implementation worktree under
`data/devscratch/backend-fixes/`. These are disposable PostgreSQL 15 checks,
not tests against an existing application database.

| Run | Result | Evidence |
| --- | --- | --- |
| Baseline fork at `c447e15d1`, migration tag excluded | 17 failures, 172 errors | `before-pg-fork-nomig/` |
| Repaired fork, migration tag excluded | 4,468 run; zero failures/errors; six environment skips | `after-pg-fork-nomig/` |
| Upstream apps, migration/performance tags excluded | 1,959 run; 14 failures; eight errors; one skip | `after-pg-upstream/` |

The green fork run predates the final flush replacement. Its manifest records
HEAD `c447e15d1` while the fixes were still uncommitted, so it is historical
working-tree evidence, not a complete green run of final commit `b2d690bc5`.
Its six skips are not additional regressions.

The upstream failures include unavailable network/package installation,
missing `scim2_client`, an absent writable `_testfolder`, missing compiled
translations/static assets, missing Git metadata and runner-specific config
paths/site URL. The admin-checkpoint assertion also predates its fix. These
causes do not turn that run into a passing upstream suite. The two remaining
suspects are addressed separately below.

## Fresh conclusion checks

Fresh baseline checks ran in the clean follow-up worktree; corrected-fixture
checks ran in the implementation worktree. All conclusion evidence is now
collected in the implementation worktree under `data/devscratch/backend-fixes/`,
with original manifests and cleanup records preserved. Every PostgreSQL invocation creates a new
internal-only network and database container; SQLite runs without networking.
Both source and configuration are isolated from owner data.

| Check | Actual result | Evidence |
| --- | --- | --- |
| Fork transaction modules plus complete `BuildConsumeTest` class, final flush | 65 tests pass; one additional label import error because the mutable image lacks `pdfminer`; no flush failures | `conclusion-pg-transactions-upstream/` |
| `BuildConsumeTest.test_consume_many_lines` within that class | Pass; allocation 31 queries against limit 50; complete 55/200; finish 124/180 | Same log |
| Complete label mixin class on original immutable test image, with static preparation | Six tests pass, including `test_printing_process` | `conclusion-pg-label-original-image/` |
| Initial full fork SQLite suite, migration tag excluded | 4,465 run; two failures; 29 errors; 106 skips, traced to timezone fixture mismatches | `conclusion-sqlite-fork/` |
| Corrected SQLite fixtures plus warning-notification backend | 70 run; zero failures/errors; four expected skips | `conclusion-sqlite-focused/` |
| Final full fork SQLite suite, migration tag excluded | 4,469 run; zero failures/errors; 106 expected skips, including warning-notification backend changes | `conclusion-sqlite-fork-final/` |
| Initial fork migration-tagged suite, fresh PostgreSQL | 24 run; two observer fixture errors; 15 named checkpoint skips | `conclusion-pg-fork-migrations/` |
| Corrected fork migration-tagged suite, fresh PostgreSQL | 24 run; nine pass; 15 named checkpoint skips; zero failures/errors | `conclusion-pg-fork-migrations-final/` |
| Upstream migration-tagged modules, fresh PostgreSQL | 14 run; 14 errors; no passing-lane claim | `conclusion-pg-upstream-migrations/` |
| Full build API and label modules, original prepared image | 71 tests pass, including both original suspects and their neighboring module cases | `conclusion-pg-upstream-order/` |
| Final warning-notification regression after marker-variable rename | 11 tests pass; isolated container using a copy of the earlier focused test database | `conclusion-notification-final/` |

The first fresh run used image
`sha256:89981388aff5f8329596f78ec9ce1bfb141ff9369e52fc7f9ae19305440fa96e`.
The original full-run image is
`sha256:ee108ee9fd58dc955d5db6951c8c64d28549c01b9886b89b5da48ab090fc98e3`;
the label rerun pins it explicitly. A mutable `latest` tag is not equivalent
to the historical environment.

The build case's earlier 241-query result is not reproducible in the fresh
class or complete API-module run: the allocation remains 31/50 queries. The
label case passes both alone and after the complete build API module on the
original image with static files prepared. Neither code regression is
reproduced. This supports environment or wider-suite order sensitivity, but
does not identify the original contaminating predecessor or prove that the
entire 1,959-case upstream suite is green. No query limit was raised and no
unrelated build or label logic was changed.

SQLite's first full run found aware timestamp writes under the upstream test
default `USE_TZ=False`, plus UTC-day cap comparisons using that incompatible
profile. Five test classes across `test_attachment_exact_target.py`,
`test_memory_lifecycle.py`, `test_seed_analysis_eval_fixtures.py` and
`test_worker_usage.py` now explicitly select `USE_TZ=True`, as production does.
No production timezone setting, service implementation, assertion or skip was
changed for that correction. The focused rerun exercises the actual database
operations, including the previously failing owner-purge and daily-cap cases.

The migration observer fixture copied the real database under an unregistered
`startup_observer` alias. PostgreSQL's connection-created type registration
then looked up that alias in Django's connection handler and failed before
either atomicity assertion could run. It now copies the valid default alias,
registers cleanup before connecting, and asserts that its physical connection
is distinct. The DDL visibility, advisory-lock and rollback assertions remain.
The first 2 GiB migration run was terminated during setup (exit 247); its
archived log remains under `history/`. The reruns use the normal 4 GiB cap.

All six final PostgreSQL cleanup records report successful removal of their
owned database container and internal network. SQLite test containers use
automatic removal. Existing databases and services were not restarted or
modified. The final 4,469-case SQLite run is new full-suite evidence; the
4,468-case PostgreSQL result remains historical, complemented by fresh
transaction and migration checks rather than claimed as a full final-HEAD run.
The full SQLite run precedes a behavior-preserving notification marker variable
rename: the staged secret scanner mistook `severity_key` for a credential.
Renaming that local identifier removes the false positive without a scanner
ignore or hook bypass; all 11 notification regressions pass afterwards.

## Reproduction

Run from the implementation worktree; the named image is already cached.
Each invocation copies source and creates a fresh disposable database. The
runner's `--keepdb` flag does not reuse a previous PostgreSQL container.

```sh
backend_test_image=sha256:ee108ee9fd58dc955d5db6951c8c64d28549c01b9886b89b5da48ab090fc98e3
python3 contrib/ui-ux/r6/backend_baseline.py --image="$backend_test_image" \
  --output=data/devscratch/backend-fixes/conclusion-sqlite-fork-final \
  --test-arg=--exclude-tag=migration_test \
  aichat approvals assets machine_health repair tasks voice legacy_boundary
python3 contrib/ui-ux/r6/backend_baseline.py --image="$backend_test_image" --postgres \
  --output=data/devscratch/backend-fixes/conclusion-pg-fork-migrations-final \
  --test-arg=--tag=migration_test \
  aichat approvals assets machine_health repair tasks voice legacy_boundary
python3 contrib/ui-ux/r6/backend_baseline.py --image="$backend_test_image" --postgres \
  --plugins --site-url=http://localhost:8000 --static \
  --output=data/devscratch/backend-fixes/conclusion-pg-upstream-order \
  build.test_api plugin.base.label.test_label_mixin
```

## Migration policy and remaining gates

The 2026-10-21 legacy checkpoint is intentionally irreversible. The handoff's
12 upstream migration cases are not the current complete inventory: the fresh
six-module run collected 14 and all errored. Its observed exceptions are 13
duplicate-table errors (`assets_demometricscoverageinterval`,
`plugin_notificationusersetting`, `part_partattachment`) during reconstruction
or reset, and one missing graph node for replaced migration
`users.0026_ruleset_operating_authority_db_default`. Later errors share a
database already affected by the first failed reset; these are not 14
independently reproduced root causes. They must not all be relabelled as
direct irreversible-checkpoint exceptions.

A successful fresh forward application does not establish historical rewind
or reset safety. Making a checkpoint operation reversible, or skipping only
an irreversible operation, has not been demonstrated sufficient to repair the
retained-table reset and replaced-leaf selection problems above.

The seven-commit branch already conditionally skips blocked fork round trips
using the migration plan. That does not decide the policy for upstream tests.
The owner was asked to choose between:

1. Leave the upstream migration CI lane off with the coverage gap explicit
   until replacement forward-migration coverage exists (recommended).
2. Skip only checkpoint-blocked upstream tests in the fork, with named reasons.
3. Permit an empty-database reverse path, requiring separate design and review
   of the checkpoint's forward-only deployment boundary.

No new upstream skip, checkpoint reversibility change or CI-lane change is
authorized by this report alone. Existing migration history is not rewritten
as part of this conclusion.

The pump warning UI is tracked separately in
[its execution ledger](pump-warning-links.md). Its local commit follows
verification and retains R6 backlog/contract dispositions. Push and PR remain
subject to the owner's go-ahead and manual human review.
