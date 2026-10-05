# AIMMS MAF harness migration workspace

This directory contains source-grounded planning and H0 setup tooling for the admin-authorized migration. It does **not** contain an already migrated application.

- [Execution plan](EXECUTION_PLAN.md): frozen baseline, actual test/dependency/Azure observations, H0–H9 gates and rollback.
- [Feature inventory](FEATURE_INVENTORY.md): nine supported registry IDs, retired WF5, live/disabled/unresolved posture and admin acceptance.
- [Supervised work packages](WORK_PACKAGES.md): GPT/MiMo responsibilities, file ownership, test-first implementation sequence and acceptance handoff.
- [Current local test command](TESTING.md): hardened existing-SDK runner, reports and explicit skipped coverage; [repair plan](TESTING_REPAIR_PLAN.md).

Approved worktree: `/home/lokesh/Documents/mbpro/InvenTree-MAF-Harness` on `feat/aimms-maf-harness`, from `equa/customizations` at `05d47269bdd58febafea6f91542ad37b54ed7fdc`. Do not use the original dirty checkout as this migration's source or dependency environment.

## Reproduce the existing focused baseline lane

This is a **historical pre-repair invocation/result**, not the current reproduction command. Use [TESTING.md](TESTING.md), which supplies both backend import roots, a coherent disposable bootstrap, environment scrubbing and complete source accounting.

The task-owned Docker environment is already prepared. This command imports the migration checkout read-only, cannot contact Azure or external services, bypasses normal container startup and puts temporary state/results only in its named volume:

```bash
docker --context default run --rm \
  --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges \
  -e TMPDIR=/scratch -e PYTHONDONTWRITEBYTECODE=1 \
  -e PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  -e DJANGO_SETTINGS_MODULE=ai.core.tests.settings \
  -e PYTHONPATH=/repo/src/backend/InvenTree \
  -v aimms-maf-harness-setup-fe1e6a7f:/scratch \
  -v /home/lokesh/Documents/mbpro/InvenTree-MAF-Harness:/repo:ro \
  -w /scratch --entrypoint /scratch/baseline-venv/bin/python \
  sha256:b6ce427887301a8d10058e59d55ee99f0e01f52ce4ef7e3e250687afa557fc07 \
  -m pytest -c /dev/null -p pytest_asyncio.plugin \
  -o cache_dir=/scratch/pytest-cache \
  --junitxml=/scratch/baseline-focused.xml -q \
  /repo/src/backend/InvenTree/ai/core/tests/test_agent_factory.py \
  /repo/src/backend/InvenTree/ai/core/tests/test_requirement_pins.py \
  /repo/src/backend/InvenTree/ai/core/tests/test_rbac_run.py \
  /repo/src/backend/InvenTree/ai/core/tests/test_capability_invocation_guard.py \
  /repo/src/backend/InvenTree/ai/core/tests/test_trusted_context.py \
  /repo/src/backend/InvenTree/ai/core/tests/test_maf_adapter.py \
  /repo/src/backend/InvenTree/ai/core/tests/test_rail_replay.py
```

Observed first result: **72 passed, 2 skipped, 1 Django-Q configuration warning**. A separate expanded run had **387 passed, 22 setup errors, 4 subtests passed, 1 warning**; it remains failed. See the execution plan for the minimal-settings migration dependency issue. The focused pass does not erase that failure or establish full application/staging parity.

The candidate interpreter is `/scratch/candidate-venv/bin/python` in the same task volume, with a separate pure modern-MAF environment. It is suitable for SDK discovery only, not application startup. Do not overlay it on the original application's environment or assume full backend compatibility.

## Evidence and limits

Durable sanitized evidence is under `/home/lokesh/Documents/mbpro/aimms-maf-migration-evidence`. It records exact commands, source identity, allowlisted Azure settings, pinned image/package probes and test results. Keep credentials, raw environment dumps, runtime configs and customer payloads out of this directory and any downloadable report.

The locally tested dependency carrier and deployed Azure image are different immutable artifacts. ARM metadata and public health checks are not authenticated feature/build/worker verification. Runtime changes, feature activation, schema changes, effects, deployments, commits and publication remain outside this setup.

The initial MiMo baseline CLI/test package is described in `WORK_PACKAGES.md`. The first handoff failed additional safety/robustness and formatting checks; those results are retained. The corrected two-file result now passes independent parent verification: **29 fixture tests**, **five supervisor regressions**, real-repository deterministic output/independent AST counts, and preview lint/format. It is retained as **partial static setup tooling**, not complete dispatch, authorization or live-feature proof. The worker exceeded its 20-minute cap (about 28.71 minutes); no further refinement pass was launched.

## Use the static inventory probe

Requires Git and a Python interpreter with stdlib `tomllib`; verified here with Python `3.13.5`. From this checkout:

```bash
# The output must be outside the selected repository; stdout is also supported.
PYTHONDONTWRITEBYTECODE=1 python3 contrib/aimms_harness/baseline_probe.py \
  --repo /home/lokesh/Documents/mbpro/InvenTree-MAF-Harness \
  --output /home/lokesh/Documents/mbpro/aimms-maf-migration-evidence/baseline-inventory.json

# Credential-free tooling tests, not application parity tests.
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s contrib/aimms_harness -p 'test_*.py' -v
ruff check --no-cache --preview contrib/aimms_harness/baseline_probe.py contrib/aimms_harness/test_baseline_probe.py
ruff format --preview --check contrib/aimms_harness/baseline_probe.py contrib/aimms_harness/test_baseline_probe.py
```

The verified report contains nine workflow definitions, one alias and 351 scoped test-source paths. It explicitly sets `inventory_complete=false`: 12 dependency lines are unparsed (four each in backend `requirements.in`, `requirements.txt` and `requirements-3.14.txt`). Counts match the emitted arrays; repeated/relative-path/different-CWD/stdout reports match byte-for-byte. A successful CLI exit means a report was emitted, **not** that the inventory or H0 is complete.

In-repository output, symlink output and multiply linked existing output files are refused. Optional source symlinks are skipped and disclosed as incomplete; required symlinked inputs/ancestors fail safely. This is trusted-local-checkout tooling: atomic output/race hardening and exotic bind-mount aliases were not proved. The parent verification receipt is `probe-corrected-parent-verification.json` in the evidence directory.

## Accepted planning decisions and remaining gates

The admin accepted the preservation inventory as written and selected an isolated test customer, reversible fixtures and mocked outbound email/orders for later `aimms-dev` acceptance tests. A frozen approved inventory and its SHA-256 are recorded in the durable evidence directory. This is planning acceptance only: specific live fixtures, deployment, account/RBAC changes, real-customer writes and external integrations remain separately gated.

H0 still needs complete dispatch/dependency qualification, corrected/qualified baseline bootstrap, actual authenticated enablement, named test fixtures and a quantitative baseline. None of H1–H9 is marked complete here. Remaining work needs a fresh scoped/timeboxed decision; no worker is running, and the previous delegation pins have been restored.
