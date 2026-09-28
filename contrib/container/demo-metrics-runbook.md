# EQUA demo metrics — one-shot Job release runbook

Operator runbook for running the tracked EQUA demo-metrics commands
(`plan/apply/verify/replay/stop/cleanup_demo_metrics`) against the **existing**
PostgreSQL database (`epconchat-pg-dev` / `inventree`, PostgreSQL 15) that
serves the Azure Container App deployment. There is no separate data product,
no `azd` scaffold, and no new database.

**Limits of this document and its artifacts:** everything here was verified
**locally and offline only** (renderer + unit tests). No cloud call, Job
provisioning, database write, seed, or deployment gate has been performed or
passed. Nothing in this document authorizes provisioning, seeding, or any
production change. Local renderer checks do not prove production behavior and
do not resolve backend or deployment gaps (see below). Design authority is
`LocalDocs/UiUpgrades/EQUA_Demo_Metrics_PostgreSQL_Implementation_Plan.md`
(sections 12–17); when this runbook and that plan disagree, the plan wins.

## Artifacts (tracked)

| File | Purpose |
|---|---|
| `contrib/container/demo-metrics-job-spec.py` | Offline renderer/validator for the one-shot Job execution spec. Never contacts Azure; never starts anything. |
| `contrib/container/demo-metrics-job-input.example.json` | Input template. Deliberately full of `REPLACE_WITH_*` tokens — rendering it **as-is must fail** (fail-closed proof). |
| `contrib/container/demo-metrics-job-spec-tests.py` | Offline tests (69) for the renderer, including backend contract drift checks. |
| `contrib/container/demo-metrics-runbook.md` | This runbook. |

Run the tests from the repository root:

```bash
python3 contrib/container/demo-metrics-job-spec-tests.py
```

The tests are stdlib-only and offline (temporary files go to the scratch
directory via `TMPDIR`, never `/tmp`). They validate the release artifact
renderer only — they are **not** backend feature tests, integration coverage,
or deployment approval. They also **parse** the tracked backend sources
(`assets/management/commands/*_demo_metrics.py`, `assets/demo_metrics/
fingerprint.py`, `planner.py`, `apply_service.py`) with the stdlib `ast`
module as drift guards for the per-command flag tables and the runtime
attestation names; Django is never imported and no management command is
executed, so these contract checks still do not replace backend tests.

## Safety properties the rendered spec enforces

The renderer refuses to produce a spec unless all of the following hold, and
bakes the fixed values in itself (they are not configurable per run):

- **Manual trigger only.** `triggerType: Manual`, `parallelism: 1`,
  `replicaCompletionCount: 1`, `replicaRetryLimit: 0`. A failed run is
  recovered with receipts and a deliberate rerun, never an automatic retry.
- **Bounded runtime.** Explicit `replica_timeout_seconds` (60–3600). Replay
  additionally requires `--max-duration-seconds` explicitly and the timeout to
  be **at least `--max-duration-seconds` + 120 seconds** (documented policy:
  `timeout >= duration + 120`).
- **Explicit startup override of both ENTRYPOINT and CMD.** The container
  `command` is `[python_interpreter]` and `args` is
  `[/home/inventree/src/backend/InvenTree/manage.py, <approved command>, ...]`.
  Setting both matters: overriding only one of them risks inheriting the
  image's other half (e.g. gunicorn arguments) via concatenation. This
  replaces the image ENTRYPOINT (`/bin/bash ./init.sh`, which performs config
  copying, venv setup, `invoke static`, and SPA bundle sync — startup behavior
  a one-shot Job must not run). `python_interpreter` must be an absolute
  python3 path.
- **Honest working-directory contract.** `working_directory` must declare
  `/home/inventree/src/backend/InvenTree` and is validated as the expected
  project layout, but it is **not applied at runtime**: Container Apps has no
  working-directory override, the image WORKDIR stays `/home/inventree`, and
  it is the **absolute `manage.py` path** that puts the Django project on
  `sys.path`. The rendered spec records this
  (`working_directory_applied_by_runtime: false`); do not treat the field as a
  runtime chdir and do not shell-wrap the command to fake one.
- **Migrations disabled.** `INVENTREE_AUTO_UPDATE=False` must be set
  explicitly; migration-ish argv (`migrate`, `makemigrations`, `update`,
  `invoke`) is refused. The **web app is the single migration owner**; the Job
  must never be a second one (plan §13.3 step 3).
- **Strict per-command argument grammar.** `command_args` is parsed against an
  explicit table mirroring each management command's `add_arguments()`
  exactly. Refused: unknown switches, duplicated switches (including two
  `--actor` values), positional extras, missing or switch-shaped values
  (`--actor --fixture …` is a refusal, not an actor named `--fixture`),
  trailing value-less flags, and Django/global switches
  (`--settings`, `--pythonpath`, `--skip-checks`, `--verbosity`, …) that could
  point the process at unreviewed configuration. Numeric flags must be finite
  (`nan`/`inf`/overflow refused) and bounded: `--max-duration-seconds` in
  (0, 3600], `--interval-seconds` in [0, 3600] and not above the duration.
  Boolean flags (`--apply`, `--include-history`) are explicit and bare — they
  take no value. `--include-history` is allowlisted on `apply_demo_metrics`
  only, matching the backend declaration. `--*-sha256` values must be 64 hex
  characters. Cleanup: `--apply` requires `--approved-cleanup-sha256`, and
  `--approved-cleanup-sha256` without `--apply` is refused.
- **Immutable, matched images.** The Job image must be pinned
  `registry/name@sha256:<64 hex>` — mutable tags (`:latest`, `repo:tag@…`) are
  refused. `expected_image_digest` must equal the pinned digest, and the
  recorded web and worker image references must share that same digest (plan
  §13.3 step 4). The provenance block records the **full**
  `image_reference`/`web_image_reference`/`worker_image_reference`, not just
  the shared digest.
- **Runtime code/image attestation — exact names, derived and cross-checked.**
  The rendered Job environment always carries `AIMMS_APPROVED_COMMIT_SHA`
  and `AIMMS_APPROVED_IMAGE_DIGEST`, the **exact** names the backend apply
  preflight requires (`assets/demo_metrics/fingerprint.py`:
  `CODE_IDENTITY_ENV` / `IMAGE_IDENTITY_ENV`). Their values are **derived
  from the approved input**: the commit attestation from `git_commit` and the
  image attestation from `expected_image_digest` (already required to equal
  the pinned digest shared by the Job, web and worker images). The input may
  restate them, but the values must match exactly — conflicting declarations
  (`ATTESTATION_CONFLICT`), blank values (`ATTESTATION_MISSING`) and
  malformed SHA values (`BAD_ATTESTATION`) are refused, so the Job never
  attests a commit or image other than the reviewed one. **Honest limitation,
  stated plainly:** these environment values are **operator declarations**
  supplied by the reviewed deployment, **not cryptographic proof** that the
  running image contains that code — a modified execution template could
  declare anything. What gives them meaning is the combination of the
  immutable-digest image review, the read-back gate, the mapping identity
  fields (gate 5), and the backend apply preflight, which compares the
  runtime attestation with the approved mapping identity and refuses an
  absent or different attestation (`CODE_IDENTITY_UNVERIFIED` /
  `CODE_IDENTITY_MISMATCH`). The backend identity gate is never bypassed.
- **Secrets by reference only, with explicit allowlists.** Secret-named
  settings (`PASSWORD`, `SECRET`, `TOKEN`, `KEY`, `CREDENTIAL`, `DSN`, …) may
  appear **only** in `secret_references`, and only under approved names
  (`INVENTREE_DB_PASSWORD`, `INVENTREE_SECRET_KEY`) as reference names
  (`[a-z0-9-]+`), never with values. Non-secret inline environment values are
  restricted to an explicit allowlist — database topology
  (`INVENTREE_DB_ENGINE/NAME/USER/HOST/PORT/OPTIONS`, TLS via
  `INVENTREE_DB_OPTIONS`), code identity (`INVENTREE_COMMIT_HASH/DATE`), and
  the minimal app configuration (`INVENTREE_AUTO_UPDATE`,
  `INVENTREE_LOG_LEVEL`); anything else (plugin/email/AI switches, unknown
  names) is refused. `INVENTREE_DB_USER` is a non-secret role name; its
  password must always come via `secret_references`. Inline values, a name
  carrying both value and reference, and credential-shaped values
  (scheme://user:pass@…, `AccountKey=`, `Bearer …`, private key blocks) are
  refused — and the credential scan runs over the **whole input document**,
  not just argv/env. Caveat, stated plainly: that scan is pattern-based
  best-effort defense in depth and **is not proof that no secret is present**;
  the real controls are the allowlists and the rule that no secret value ever
  belongs in the input, the rendered spec, this runbook, or any plan/README.
- **No unresolved placeholders.** Angle brackets and `REPLACE_WITH`/`CHANGEME`/
  `TODO`/`PLACEHOLDER` tokens anywhere in the input are refused — the shipped
  example is designed to fail here.
- **Exact input shape and scalar types.** Unknown keys are refused, so no
  setting can be smuggled in outside this contract. Every scalar is
  type-checked (`None`, numbers, or objects are refused — never coerced via
  `str(...)`); the environment resource ID must be a full
  `/subscriptions/<uuid>/…` identifier with a real UUID, not merely 36
  hex-or-hyphen characters.
- **Safe output handling.** The renderer never overwrites an existing output
  file (a reviewed artifact is preserved; a collision prints
  `refused: OUTPUT_EXISTS`), file-write failures print a sanitized
  `refused: BAD_OUTPUT_FILE`, and `--check` validates without writing anything
  and reports `validated: …` (it never claims to have rendered).

## Rendering the spec

```bash
# 1. Copy the example and replace every REPLACE_WITH_* token with reviewed values.
cp contrib/container/demo-metrics-job-input.example.json /private/path/job-input.json

# 2. Validate only (writes nothing; prints "validated: ..."):
python3 contrib/container/demo-metrics-job-spec.py \
  --input /private/path/job-input.json --out /private/path/job-spec.json --check

# 3. Render the spec for review (refuses if job-spec.json already exists):
python3 contrib/container/demo-metrics-job-spec.py \
  --input /private/path/job-input.json --out /private/path/job-spec.json
```

A refusal prints exactly one line, e.g. `refused: MUTABLE_IMAGE`, and exits
non-zero. Keep input and spec files outside the repository; neither contains
secrets, but they are deployment records.

Input fields (all required, all non-secret):

| Field | Source |
|---|---|
| `job_name`, `resource_group`, `location` | Deployment records; job name `aimms-demo-metrics` per plan §13.2. |
| `environment_resource_id` | Exact resource ID of the existing `epcon-ai-env` environment (plan §2/§13.2); subscription segment must be a full UUID. |
| `git_commit` | 40-hex reviewed commit the image was built from. |
| `image_reference` / `expected_image_digest` | The newly reviewed immutable digest (plan §13.2; the currently serving digest is a baseline to inspect, not the future image). |
| `web_image_reference` / `worker_image_reference` | Same reviewed digest, from the deployment record; full references are recorded in provenance. |
| `python_interpreter` | Absolute python3 path verified inside the reviewed image (see preflight below). The image installs dependencies under `/root/.local` (`PATH=/root/.local/bin:$PATH`) with system Python — verify, do not assume. |
| `working_directory` | Must be `/home/inventree/src/backend/InvenTree` — a validated **declaration** of the project layout. It is not applied at runtime (see the working-directory contract above). |
| `command` / `command_args` | One of the six commands with its real flags, matching the management commands' declared options exactly (see workflow below). |
| `replica_timeout_seconds` | 600 for plan/apply/verify/stop/cleanup; ≥1920 for a 30-minute replay (2100 per plan §13.2). Policy: `timeout >= duration + 120`. |
| `environment` | Explicit non-secret settings from the allowlist only; `INVENTREE_AUTO_UPDATE=False` mandatory. The two runtime attestation declarations (`AIMMS_APPROVED_COMMIT_SHA`, `AIMMS_APPROVED_IMAGE_DIGEST`) may be restated here but must equal `git_commit` / `expected_image_digest`; when omitted they are derived from those reviewed fields and rendered automatically. |
| `secret_references` | Approved secret names only (`INVENTREE_DB_PASSWORD`, `INVENTREE_SECRET_KEY`) with approved reference names. Names come from the deployment secret-management path; never list or export secret values to build this file. |

## Approval gates (each is separate and explicit)

1. **Image review gate** — code, fixture adapter, migrations and tests
   reviewed at the recorded commit; immutable image built; digest recorded.
2. **Local startup preflight (offline, on the reviewed image)** — inspect the
   interpreter and paths the renderer will bake in:

   ```bash
   docker run --rm --entrypoint /bin/bash IMAGE@sha256:DIGEST -lc \
     'command -v python3; python3 -c "import django; print(django.__version__)"; ls /home/inventree/src/backend/InvenTree/manage.py'
   ```

   Confirm the image's Python imports Django from the image environment.
   Do not loosen firewall/TLS/auth to make anything pass (plan §13.1).
3. **Job provisioning gate** — provisioning `aimms-demo-metrics` is a **new
   resource** and needs its own approval (plan §13.2); this runbook does not
   authorize it. The rendered envelope is a **review artifact, not an Azure
   ARM/CLI document as-is**: an approved deployment step (CLI, ARM/Bicep, or
   Terraform — whichever the deployment workstream approves) consumes it by
   translating the fields below, and the envelope's `document_contract` block
   says so. Mapping onto `az containerapp job create` inputs (verify flags
   against the installed CLI's `az containerapp job create -h`;
   containerapp extension 1.3.0b2 was observed — do not assume):

   | Rendered spec field | Deployment input |
   |---|---|
   | `job.name` / `job.resource_group` / `job.location` | `--name` / `--resource-group` / `--location` |
   | `job.properties.environmentId` | `--environment` |
   | `template.containers[0].image` | `--image` (digest-pinned reference, verbatim) |
   | `template.containers[0].command` | `--command` (the single interpreter token) |
   | `template.containers[0].args` | `--args` (one CLI argument per array token — quote each token individually; never re-join the array into a shell string) |
   | `configuration.triggerType`, `manualTriggerConfig`, `replicaRetryLimit`, `replicaTimeout` | `--trigger-type Manual --parallelism 1 --replica-completion-count 1 --replica-retry-limit 0 --replica-timeout <TIMEOUT>` |
   | `env` entries with `value` | `--env-vars NAME=VALUE` |
   | `env` entries with `secretRef` | `--env-vars NAME=secretref:<SECRET_NAME>`; the secret values themselves via the approved secret path (below) |

   If the installed CLI cannot express `command`/`args`, do **not** shell-wrap
   the array into a single string; consume the same two fields through the
   approved ARM/Bicep/Terraform template instead
   (`properties.template.containers[0].command` / `.args`).

   Configure the secret values themselves through the established deployment
   secret-management path (pipeline or `az containerapp job secret set`
   performed by the approved process) — never on a shared command line, never
   in a tracked file, never by copying the web app's environment. Being in
   `epcon-ai-env` does not copy the app's identity, secrets, or settings
   (plan §4/§2). Restrict Job start authority to trusted operators: starting a
   Job permits execution-template overrides and access to its configured
   secrets/identities (plan §13.2).
4. **Read-back gate** — after creation, verify the exact configuration with a
   projected read-only query. The projection includes `command`, `args`, and
   environment **names / secretRef metadata only** — never `--show-secrets`,
   never environment values, never a full container configuration export:

   ```bash
   az containerapp job show --name aimms-demo-metrics --resource-group <RESOURCE_GROUP> \
     --query '{name:name,trigger:properties.configuration.triggerType,retry:properties.configuration.replicaRetryLimit,timeout:properties.configuration.replicaTimeout,parallelism:properties.configuration.manualTriggerConfig.parallelism,completion:properties.configuration.manualTriggerConfig.replicaCompletionCount,image:properties.template.containers[0].image,command:properties.template.containers[0].command,args:properties.template.containers[0].args,envNames:properties.template.containers[0].env[*].name,envSecretRefs:properties.template.containers[0].env[?secretRef!=null].{name:name,secretRef:secretRef}}' -o json
   ```

   Compare `command`, `args` and the env name/secretRef lists against the
   rendered spec. Any mismatch fails the gate. Also confirm the Job is **not**
   started and no schedule/trigger exists.
5. **Mapping approval gate** — the resolved target mapping (which six
   machines/locations, synthetic ownership, user bindings) is produced and
   reviewed per plan §6 and the backend feature's mapping contract. No
   credentials in the mapping. Record its hash (the commands record
   `mapping_sha256` in the session). Record `target.approved_commit_sha` /
   `target.approved_image_digest` in the mapping **exactly** as the rendered
   spec's `git_commit` / `expected_image_digest`: the apply preflight compares
   the Job's attestation environment against those values and refuses an
   absent or different attestation (`CODE_IDENTITY_UNVERIFIED` /
   `CODE_IDENTITY_MISMATCH`).
6. **Plan gate** — run `plan_demo_metrics` (read-only by design; classified
   read-only in `InvenTree/ready.py`). Review the plan artifact, then approve
   its `plan_hash` value out of band.
7. **Apply gate** — exactly one manual execution with
   `--approved-plan-sha256 <plan_hash>`. Retry limit is 0: a failed or lost
   run is reconciled from receipts and deliberately re-run, never auto-retried
   (plan §13.2).
8. **Verification gate** — `verify_demo_metrics` receipts, scoped API
   read-back, and browser acceptance on the serving application (plan §13.3
   step 8). Optional bounded replay only after its own checks pass.
9. **Cleanup gate** — reviewed cleanup plan, approved `plan_hash`, single
   `--apply` (see rollback below).

## CLI workflow (flags as implemented)

All commands run as the reviewed image's one-shot `command`/`args` (or in a
disposable local database for testing — never the shared target). From
`/home/inventree/src/backend/InvenTree`:

```bash
# Plan (read-only; writes only the plan artifact)
python3 manage.py plan_demo_metrics \
  --fixture /inputs/demo_fixture.json \
  --mapping /inputs/target_mapping.resolved.json \
  --out /outputs/plan.json \
  --actor <OPERATOR_USERNAME>

# Approve the plan_hash printed inside /outputs/plan.json, then apply ONCE
python3 manage.py apply_demo_metrics \
  --fixture /inputs/demo_fixture.json \
  --mapping /inputs/target_mapping.resolved.json \
  --plan /outputs/plan.json \
  --approved-plan-sha256 <APPROVED_PLAN_HASH> \
  --actor <OPERATOR_USERNAME>            # add --include-history only if approved

# Verify receipts, records, scope and counts (read-only)
python3 manage.py verify_demo_metrics --session <SESSION_KEY> --actor <OPERATOR_USERNAME>

# Optional bounded replay (only for approved machine set; timeout >= duration + 120)
python3 manage.py replay_demo_metrics --session <SESSION_KEY> --actor <OPERATOR_USERNAME> \
  --interval-seconds 30 --max-duration-seconds 1800

# Revoke future replay authority
python3 manage.py stop_demo_metrics --session <SESSION_KEY> --actor <OPERATOR_USERNAME>

# Cleanup: plan first (read-only), review, then apply with the approved hash
python3 manage.py cleanup_demo_metrics --session <SESSION_KEY> --actor <OPERATOR_USERNAME> \
  --out /outputs/cleanup-plan.json
python3 manage.py cleanup_demo_metrics --session <SESSION_KEY> --actor <OPERATOR_USERNAME> \
  --apply --approved-cleanup-sha256 <APPROVED_CLEANUP_HASH>
```

Notes grounded in the implemented commands (`assets/management/commands/`,
`assets/demo_metrics/cli.py`):

- `--actor` is mandatory everywhere and must name the trusted operator; the
  commands resolve it and refuse an empty/unresolved value. Angle-bracket
  placeholders in arguments are refused at runtime too.
- `--approved-plan-sha256` / `--approved-cleanup-sha256` must equal the
  `plan_hash` embedded in the reviewed artifact (recomputed server-side from
  the canonical body; a mismatch fails the command). They are integrity
  checks, **not** authorization — the human approval is gate 6/9.
- `cleanup_demo_metrics` is read-only unless `--apply` is present
  (`InvenTree/ready.py` classifies it accordingly).
- `--include-history` is declared by `apply_demo_metrics` only (as of the
  reviewed commit). `plan_demo_metrics` has **no** `--include-history` flag:
  the plan body records `include_history` from the resolved mapping's
  `history_import_approved`, and apply's separate `--include-history` flag is
  refused by the backend (`HISTORY_NOT_APPROVED`) when the approved plan body
  does not authorize the synthetic history import. The renderer's option
  tables mirror the commands' `add_arguments()`; if a command gains or moves
  a flag (e.g. `--include-history` on plan during hardening), the renderer
  table and this runbook must be re-reviewed together — the renderer will
  refuse the new flag until then, and the offline contract tests fail on the
  drift.
- Each operation needs its own Job execution (one command per rendered spec).
  Re-render the input with the next command and repeat gates as applicable.

## Evidence to retain (no secrets)

Record: image digest and `git_commit`, the rendered spec, Job read-back JSON
(names/secretRef metadata and command/args only), resolved mapping and its
hash, the plan artifact and approved `plan_hash`, execution/session key,
`verify_demo_metrics` output, scoped API results, and browser screenshots.
Never record: secret values, connection strings, cookies, environment values
from the Job, or full container configuration exports. Session cookies for
browser acceptance follow the existing `AIMMS-release-checks.md` procedure and
are deleted afterwards.

## Rollback and cleanup (plan §16)

Default rollback is **stop replay + disable session UI + reviewed targeted
cleanup**, not a database restore:

- Run `stop_demo_metrics` before anything else if a replay is active.
- `cleanup_demo_metrics` deletes only unchanged session-created rows listed in
  the approved cleanup plan. Borrowed machines/locations/users, existing
  controls, other sessions' records, operator-modified rows and governed
  evidence are retained/reported, never force-deleted. A name prefix or tag is
  never a cleanup selector.
- Application image rollback does not undo PostgreSQL data or migrations;
  initial migrations are additive and compatible with the rollback image. Do
  not drop receipt/history tables to hide a failed rollout.
- Azure PostgreSQL PITR restores to a **new server**, not just this demo —
  treat it as separately approved disaster recovery (plan §16). Capture the
  pre-apply recovery point; no restore has been tested here.

## Local whole-process bootstrap/read-only probe (subgate)

`contrib/container/demo-metrics-bootstrap-probe.sh` proves the **local** part
of plan §7.2: each read-only command (`plan_demo_metrics`,
`verify_demo_metrics`, `cleanup_demo_metrics` WITHOUT `--apply`) runs as a
**fresh process** whose entire lifetime — interpreter startup (`sitecustomize`
guard), Django setup, plugin/app `ready()` paths, `handle()` — executes under
two server-enforced read-only layers against the DISPOSABLE database
`inventree_dm_bootstrap_v1` (never the default/shared/cloud database; the
probe settings shim refuses any other name or a cloud-shaped host):

1. the connection role `dm_bootstrap_ro` holds CONNECT + SELECT only, so any
   write anywhere in the process — bootstrap, `handle()`, auxiliary
   connections, task enqueue — is rejected by the server;
2. `default_transaction_read_only = on` is set database-wide as a second
   server-side layer.

Per-table row-COUNT fingerprints (275 tables) are captured before/after the
window (row counts only — this is NOT a content checksum; in-place content
updates are not detected here and are prevented by the server-side read-only
defense instead), every outbound socket connect is recorded and anything off
the harness-local allowlist is blocked + recorded (observe/block+record
instrumentation of external transport only — no production behavior is
monkeypatched or suppressed), the process output is scanned for server-side
write markers, and the `log_statement=all` server statement log is captured
and gated: any write-shaped statement or write-refusal error in the read-only
window fails the probe, even when the server caught the attempt and the
process swallowed the failure. Negative probes demonstrate the defenses
actually CATCH an attempted ORM write and an attempted external connect —
server-side, with the attempt and its refusal both in the statement log. The
orchestration is FAIL-CLOSED: nonzero probe exit codes are propagated as the
harness exit code (every status recorded in `exit_codes.txt`), expected
artifacts and logs must exist and be non-empty, every process's transport log
must show `guard_installed` and — outside the negative probe — zero blocked
connect attempts (an attempted forbidden transport fails the harness even if
the guard caught it and the command survived). The harness first runs
RED/GREEN self-tests of these validators
(`contrib/container/bootstrap_probe/harness_validators.py` +
`test_harness_validators.py`; fabricated unit-test fixtures — NOT acceptance
evidence). SHA-256 parity of every file under test (host tree vs container
mount) is verified before the run. Run from the repository root:
`bash contrib/container/demo-metrics-bootstrap-probe.sh` (dev container +
disposable PostgreSQL; artifacts under
`~/.hermes/cache/scratch/dm_bootstrap_probe-*`).

**Result at 2026-09-26/27 (local dev runtime: dev container venv, Python 3.12,
Django 5.2.17, PostgreSQL 15 `pgvector/pgvector:pg15`), fail-closed harness:**

- harness self-tests: 18 RED/GREEN validator cases pass (fabricated unit-test
  fixtures, not acceptance evidence); SHA-256 parity verified (20 files);
- governed setup apply created session `dm-bootstrap-1` (2 sources, 15
  bindings, 7 work orders, 12 observations, 2 controls, 39 receipts) after a
  data reset of ONLY the disposable database (never dropped; default/shared
  database untouched), then the read-only window was armed (`log_statement`
  confirmed set — the earlier run's capture bug is fixed);
- `plan_demo_metrics` exit 0 (`plan_sha256=64b993bf…`, 11 conflicts correctly
  reported — the active session already claims the machines),
  `verify_demo_metrics` exit 0 (full readback report, 39 receipts returned),
  `cleanup_demo_metrics` (plan mode, through the new
  `build_cleanup_plan(session, actor)` authorization gate) exit 0
  (`cleanup_plan_hash=f615f050…`, 26 deletions, 0 retained-modified) — three
  fresh processes, every status in `exit_codes.txt`;
- fingerprints identical before/after: 2549 rows in 275 tables — zero
  row-count changes (row COUNTS only, not a content checksum);
- negative probes PASSED and server-visible: the attempted
  `INSERT INTO "assets_client"` reached the server and was refused there
  (`cannot execute INSERT in a read-only transaction`); the attempted external
  connect was blocked + recorded;
- transport guard loaded in every process (`guard_installed` in all six
  netlogs); zero Python-level connect attempts outside the negative probe;
  exactly one blocked attempt, the negative probe's (expected).

**Historical failure: exit 1 at the server-side statement-log gate — a true
positive the pre-gate harness could not see. Locally fixed below:**
the read-only window contains one
`INSERT INTO "common_inventreesetting" ("value", "key") VALUES ('',
'_AIMMS_ROLLBACK_FLOOR_ARMED')` per read-only command process
(plan/verify/cleanup), each immediately refused with `cannot execute INSERT in
a read-only transaction` and each **swallowed** in process output (the output
marker scan sees nothing). Source: the `check_rollback_floor` system check
(`aichat/apps.py`) reads the marker via `InvenTreeSetting.get_setting`, whose
`get_setting_object(create=True)` path INSERTs the row when absent
(`common/models.py`); the refusal surfaces as `InternalError` and is caught by
the check's broad `except Exception`. A second ensure-on-read write path,
`plugin/lease.py try_acquire_lease` (`get_or_create` of
`_PLUGIN_REGISTRY_HASH`), fires during plugin registry reload in
`manage.py shell` processes (visible in the fingerprint/negative output as
`Unexpected error during plugin reload`). Both manifest when the rows are
absent (cold-start state). The server's SELECT-only/read-only defenses reject
these writes; matching row counts alone do not prove unchanged contents.
Supervisor review of that three-command SQL window found three marker INSERTs
and no lease-claim statements. The correction below makes the lookup non-creating
without disabling armed-floor enforcement. The plugin-lease errors belong to
the separate shell-based evidence helpers, not proven write attempts in the
plan/verify/cleanup window; assess that helper behavior separately rather than
changing unrelated plugin behavior to make the main gate green. The harness
does not mask the observed failures.

**Corrected local checkpoint:** `check_rollback_floor` now reads with
`get_setting(ROLLBACK_FLOOR_SETTING, '', create=False)`. Supervisor review
confirmed intentional arming and flag enforcement are unchanged; an independent
run of all eight rollback-floor tests and scoped Ruff check/format passed.
MiMo reran the probe successfully; actual evidence is in
`~/.hermes/cache/scratch/dm_bootstrap_probe-20260926-231415-1124355/`.
Supervisor independently parsed the artifacts: all six process exit codes are
zero, the command SQL window has zero write statements/refusals and exactly
three marker SELECTs, and every transport log contains its guard marker with
zero blocked attempts except the expected negative control. The negative
INSERT and server refusal are present. The read-only-role row counts match
at 2549 rows / 275 tables; they are not content checksums.

Fingerprint/negative shell helpers still log refused startup writes/plugin
reload errors; these are outside the three-command window and are not claimed
as zero-attempt helpers. This is a reviewed worker run on the development
runtime, not a supervisor rerun or reviewed-release-image approval. Its recorded
source hashes matched at supervisor review, but command-authorization edits
were in progress; repeat the probe on the final snapshot and release image.

**Earlier-run correction (2026-09-26 run, "2561 rows / 275 tables").** Its
"zero write markers … nothing was even attempted" claims were process-output
claims only and are withdrawn as server-side claims: the statement-log capture
came back empty because of two harness bugs since fixed (the `log_statement`
value lost its quoting in the enclosing shell string, and `docker logs
--since/--until` received timezone-less stamps that shifted the window ~5h).
Like the new run's counts, its 2561/275 counts are row counts only — neither
run can claim zero content changes from counts alone.

**Honest limits of this subgate.** It ran on the **local dev runtime**, not a
release image. The transport guard observes Python-level sockets only — a
native-extension connection (libpq) bypasses the socket wrapper (its target is
the allowlisted database itself and is covered by the server-side role/statement
defenses). Statement logging is per-database (set only on the disposable DB),
so write-shaped `LOG: statement|execute` lines are the probe processes' own,
but the capture shares the db container's log stream with unrelated
connections (e.g. the collation-version warnings) which the gates filter. The
probe covers startup + handle of the three read-only commands; it does not
exercise apply/replay/stop, shared-worker consumers, or any browser path. A
focused authorization workstream (verify scope-before-receipts/stdout,
stop/feed-start scope under lock, plan no-artifact denial) was still landing
during this run — the evidence applies to the tree recorded in
`sha256-parity.txt` and the probe must be re-run after those changes.

**Smallest real release-image preflight** (from `contrib/container/Dockerfile`
and `init.sh`; replaces nothing above): on the immutable reviewed digest —
(1) `docker run --rm --entrypoint /bin/bash IMAGE@sha256:DIGEST -lc
'command -v python3; python3 -c "import django; print(django.__version__)";
ls /home/inventree/src/backend/InvenTree/manage.py'` — dependencies live under
`/root/.local` with `PATH=/root/.local/bin:$PATH` and system Python
(`python:3.14.7-slim-trixie` base; the dev venv `/home/inventree/dev/venv` is
NOT what the production stage runs); (2) because the Job overrides the image
ENTRYPOINT (`/bin/bash ./init.sh`) and CMD, confirm the approved environment
carries what `init.sh` would have done (`config_template.yaml` copy, secret key
file, `invoke static`/collectstatic, SPA bundle sync) or that its absence is
acceptable for a one-shot Job; (3) re-run this whole bootstrap probe **inside
the reviewed image** against a disposable database with the same read-only
defenses. Only (3) closes plan §7.2 on the release image.

**Release-image gate: PENDING — exact provenance.** No immutable reviewed
production image containing this implementation exists. The Azure-refresh
snapshot (2026-09-26) still shows app + worker on
`aimms-hjcxb6epgvhgbyge.azurecr.io/experimental@sha256:fcef1c02…a5bb4ce1`
at commit `dcc8227940…` — the pre-implementation baseline to inspect, not an
implementation image. Local images (`inventree_devcontainer-inventree:latest`,
the devcontainer runtime image) are development artifacts and cannot satisfy
the image-review gate. Before this probe can claim release readiness: build the
implementation at a reviewed commit (with migrations + fixture adapter),
record its immutable digest (runbook gate 1), then run the preflight above and
this probe on that digest. Do not claim production bootstrap safety from the
dev image; the local result above is recorded as a separate local subgate.

## Known gaps to resolve before any execution (flagged, not edited)

These are outside this artifact set and belong to the backend/deployment
workstreams. None of them are resolved by the renderer, its tests, or this
runbook:

1. **Durable artifacts: partly covered, partly open.** PostgreSQL-backed
   receipts and ledger rows for an applied session already persist in the
   shared database (the session/receipt models are durable today). What is
   **not** durable is filesystem output: plan and cleanup-plan artifacts
   (`--out` JSON) are written inside the Job container's ephemeral filesystem.
   Plan §13.2 requires durable, hash-verified artifacts — an approved artifact
   location (mount or copy step) and its handling must exist before apply.
2. **Startup preflight probe.** Plan §13.1 requires read-only checks from the
   Job runtime (database/vendor identity, effective role, TLS, server version,
   required migrations, permissions, target record resolution). No command
   provides this yet; add one or define the approved read-only procedure
   before the first Job run. The local bootstrap probe above closes the local
   startup/read-only subgate only; the in-image run and the runtime-identity,
   TLS and target-resolution checks remain open.
3. **Configuration material under the init.sh bypass.** `init.sh` normally
   copies `config_template.yaml` and provisions the secret key file before
   `exec "$@"`. With the ENTRYPOINT bypassed, the Job must receive equivalent
   approved configuration/secret references explicitly. The renderer enforces
   the reviewed minimal allowlist (DB topology, code identity,
   `INVENTREE_AUTO_UPDATE=False`, plus secret references
   `INVENTREE_DB_PASSWORD`/`INVENTREE_SECRET_KEY`); confirm with the feature
   workstream that this set is sufficient and minimal before gate 3 (never
   copy unrelated email/AI credentials — plan §13.1).
4. **Database credentials for the Job identity.** The app's system-assigned
   identity is not inherited (plan §2 consequence 4); the Job's PostgreSQL
   access must be provisioned through the approved secret-management path and
   verified in preflight — least privilege, no implicit inheritance.
5. **Mapping resolution tooling.** This runbook consumes a reviewed
   `target_mapping.resolved.json`; its generation/validation lives in the
   backend feature (`assets/demo_metrics/`). Confirm its contract before gate 5.
6. **External-effects guard.** Plan §12's shared-worker side-effect policy
   must be proven by the feature tests before apply; this runbook cannot
   substitute for it.
7. **Cleanup-plan readback authorization — cleanup surface fixed; broader
   readback audit NOT final.** `cleanup_demo_metrics` plan mode now enforces
   the shared authorization gate before emitting the deletion / retention
   inventory: `build_cleanup_plan(session, actor)` is mandatory and requires
   an active actor with `PLAN_WORKORDER` and every membership scope
   (`assets/demo_metrics/cleanup.py`; verified by `CleanupAuthorizationTest`,
   8 tests with source parity, and exercised with the authorized
   `demo-operator` actor in the bootstrap probe rerun above — cleanup plan
   mode exit 0 through the gate). The old defect (any active user who knew a
   session key could read the plan inventory) is closed for the cleanup
   readback only. Do NOT treat the global authorization audit as final: a
   focused workstream was still landing `verify_demo_metrics`
   scope-before-receipts/stdout, `stop_demo_metrics`/feed-start scope under
   lock, and plan no-artifact denial while the probe rerun above executed;
   re-run the bootstrap probe after those changes before closing this item's
   neighboring surfaces. Note also the precise effect-boundary claim: zero
   writes SUCCEED in read-only processes (row-count-verified), while
   bootstrap-time write ATTEMPTS from ensure-on-read settings paths are a
   tracked finding in the probe section above.
8. **Attestation values are not read back.** The read-back gate projects
   environment **names** and secretRef metadata only (values are never
   displayed), so the *values* of `AIMMS_APPROVED_COMMIT_SHA` /
   `AIMMS_APPROVED_IMAGE_DIGEST` cannot be verified through that projection.
   Their consistency rests on the renderer (derived from the reviewed
   identities), gate 5's mapping identity fields, and the backend apply
   preflight comparison. Treat any execution-template override or deployment
   step that edits these env values as a re-review trigger, and prefer a
   deployment step that copies the rendered `env` block verbatim.

## What was actually verified for this deliverable

- `python3 contrib/container/demo-metrics-job-spec-tests.py`: **69 tests
  passed** (offline, stdlib-only) covering the safety properties listed above,
  including regression tests for: `--settings`/`--pythonpath` smuggling,
  duplicate switches (two `--actor` values), missing/switch-shaped flag values,
  trailing value-less hash flags, non-finite/overflow numeric flags
  (`nan`/`inf`/`1e400`), interval/duration bounds, bare boolean flags,
  `str(None)` scalar coercion, `secret_references` with a `None` name
  (previously an uncaught `TypeError`), full-UUID resource IDs, credential
  shapes outside argv/env (previously accepted in `resource_group`), explicit
  environment/secret allowlists, no-overwrite output, and `--check` reporting
  `validated:` instead of `rendered:`.
- Runtime attestation coverage was added red-green: with the tests in place
  and the renderer unchanged, 12 of the new tests failed (5 failures + 7
  errors: names absent, no derivation, no refusal codes); after deriving
  `AIMMS_APPROVED_COMMIT_SHA` / `AIMMS_APPROVED_IMAGE_DIGEST` from the
  reviewed identities and validating explicit declarations, all pass. The
  refusal codes are `ATTESTATION_CONFLICT`, `ATTESTATION_MISSING` and
  `BAD_ATTESTATION`.
- Backend contract drift checks parse the tracked sources offline (stdlib
  `ast` only): the per-command flag tables must equal the six commands'
  `add_arguments()` exactly (flag names and bare-vs-value kinds),
  `plan_demo_metrics` must declare no `--include-history` while
  `apply_demo_metrics` does, the attestation env names must equal
  `fingerprint.py`'s `CODE_IDENTITY_ENV` / `IMAGE_IDENTITY_ENV`, the plan
  body must derive `include_history` from `mapping.history_import_approved`,
  and apply must keep refusing history without plan authorization
  (`HISTORY_NOT_APPROVED`).
- Both Python artifacts pass `ruff check` and `ruff format --check` with the
  repository configuration (container ruff, scoped to these two files).
- The shipped example input is refused (`UNRESOLVED_PLACEHOLDER`), proving the
  fail-closed path.
- Command names and flags in this runbook were read from the actual management
  commands (`plan_demo_metrics`, `apply_demo_metrics`, `verify_demo_metrics`,
  `replay_demo_metrics`, `stop_demo_metrics`, `cleanup_demo_metrics`) and their
  shared helpers in `assets/demo_metrics/cli.py`; the offline suite now parses
  those `add_arguments()` declarations and the fingerprint attestation
  constants so drift fails the tests rather than the Job.
- **Not** verified: any Azure resource, any database behavior, Job creation,
  the feature's end-to-end behavior, or production readiness. This is local
  verification only, and it does not close any of the known gaps above.
