# AIMMS release verification

Build the frontend and backend from the same reviewed Git commit. Pass the full
commit through the Docker build argument `commit_hash`; the frontend build emits
`/static/web/build-info.json`, and the authenticated backend exposes
`/api/aichat/ui/capabilities/`. Local builds with uncommitted changes deliberately
report `dirty: true` and cannot pass the release gate.

## Authenticated candidate check

Sign in to the candidate revision host with an operator who has the intended
maintenance role and scope. Export that host's session cookies in Netscape cookie
file format to a private file outside the repository. The proposals endpoint
requires session authentication; an API token is insufficient.

From the repository root, run:

```bash
python .github/scripts/check_aimms_release.py \
  --base-url https://CANDIDATE_HOST \
  --cookie-file /private/path/session-cookies.txt \
  --expect-commit FULL_40_CHARACTER_GIT_SHA
```

The command performs only GET requests. It checks JSON health responses, matching
frontend/backend commit identifiers, AI thread and voice capability contracts,
proposal access, Risk Radar availability, and all 15 maintenance metric
contracts. Disabled voice is valid; disabled Risk Radar must agree with the
backend capability response. An unresolved maintenance scope, authentication
failure, HTML response, redirect, or mismatched build fails the check. Output
contains only the verdict and sanitized failure information.

Run this check before moving ordinary traffic, then repeat against the normal
application host using a session valid for that host. Confirm Azure's active
revision, image digest, replica readiness, and traffic allocation separately.
Remove the exported cookie file when verification is complete.

This checks feature contracts and access; reconcile displayed counts with the
same user's maintenance and machine records separately. It does not validate
write workflows, worker compatibility, or performance at production scale.

## Worker alignment

Inventory every intended worker before promoting an image. Record its image
digest, startup command and arguments, queue or cluster, migration setting,
environment, mounts, heartbeat, failed-task count, and backlog. Do not include
secret values in the deployment record.

After reviewing schema and scheduled-task compatibility, update the generic
worker to the same immutable release digest as the web app. Preserve `invoke
worker`, its existing queues and configuration, and
`INVENTREE_AUTO_UPDATE=False`; worker alignment must not create a second
migration owner. Check a fresh heartbeat and bounded representative task
completion, and compare failures and backlog to the captured baseline.

The optional `ai-memory` cluster is separate from the generic worker. When it is
intended to run, verify its consumer and use
`python manage.py memory_worker_status --fail-on-unready` from the backend
directory. Image alignment alone does not enable or validate that cluster. Keep
feature flags and schedule installation outside an image-only rollout.

## Local development

Use `invoke dev.asgi-server --no-reload` for the combined Django and AI service
on port 8000, alongside `invoke dev.frontend-server` on port 5173. Django's
`invoke dev.server` command does not mount the AI service. Check JSON from
`/health/live` and `/health/ai-ready`; an HTML page with status 200 does not prove
that the ASGI health endpoint was reached.

If local `db` or `redis` hostnames stop resolving, restore Docker's embedded DNS
before restarting the services. Repair external DNS upstream rather than
replacing Docker service discovery with a public resolver.
