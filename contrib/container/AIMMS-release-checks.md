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

## Live voice routing

The browser's provider RTC connection does not pin its separate HTTP controls
to a backend process. Speech dispatchers, event gates and turn locks currently
live in one process. The October 26 campaign reached different replicas for the
same session, losing correlated lifecycle acknowledgements. A healthy revision
alone does not qualify this topology.

Before enabling the repaired candidate:

1. Apply `voice.0012_voicesession_transport_owner` before serving the new code.
   It adds a private process owner with an empty database default. Existing
   sessions must finish or be ended before the rollout; users then start new
   sessions. Never migrate an active provider connection to another process.
2. Keep `INVENTREE_GUNICORN_WORKERS=1`. In the existing Multiple revision
   release strategy, set the candidate's maximum replicas to **1** and route
   100 percent of ordinary application traffic to that exact candidate.
   Do not overlap active voice sessions with a traffic switch. Confirm the
   actual serving revision's scale and process settings, not just the app's
   latest template.
3. If multiple replicas are required, use **Single** revision mode and HTTP
   sticky sessions (`affinity: sticky`). Azure supports cookie affinity only
   in Single revision mode; this changes the release strategy. Pod loss can
   still break affinity, requiring a user restart. See the official
   [session affinity documentation](https://learn.microsoft.com/azure/container-apps/sticky-sessions).
4. Save a sanitized Container App `show` response containing only revision
   mode, ingress transport/stickySessions/traffic, scale and the web container's
   `INVENTREE_GUNICORN_WORKERS` entry. Run
   `python contrib/testing/check-voice-topology.py /private/path/topology.json`.
   A nonzero result blocks voice qualification. Never put the complete env or
   secret-bearing application response in a report or repository.
5. Verify exact frontend/backend commit and image digest, then run a fresh
   campaign. Require ten distinct useful turns with paired lifecycle IDs;
   test pause/unmute, PTT, cancellation, session bounds and explicit restart
   after ownership/closed-channel failures. Compare stock and scoped counts
   with independent native reads. Record actual hearing and physical audio
   routes separately. Requested playback is not hearing acceptance.

For the current Multiple revision strategy, the candidate deployment must
include `--min-replicas 1 --max-replicas 1` alongside its reviewed image digest
and revision suffix. Do not apply a partial YAML template that could replace
other environment, probe, resource or mount settings. The read-only guard does
not deploy or change traffic.

`VOICE_SESSION_OWNER_MISMATCH` refuses a request before admitting its turn or
creating a provider channel. The browser stops capture, checks chat and asks
the user to restart. It never automatically resubmits a business request.
Closed-channel prompt/answer/presentation responses expose the same recovery
signal. Existing authorization, consent, transcript review, playback proof and
approval requirements remain in force.

For rollback, drain sessions first and restore the reviewed code/image and
routing. The previous ORM can insert rows with the added column present;
retain the additive migration during an application rollback. Remove the
column only after no running code references it. Local SQLite operation and
old-writer checks do not replace candidate PostgreSQL migration qualification.

## Markdown evidence viewer

Indexed `.md` and `.markdown` attachments can be previewed in the evidence viewer
only when their ingest uses the DOC pipeline. Metadata and bytes each recheck the
current role, client scope, and indexed source hash. The viewer pins its byte
request to the metadata revision and requests `Range: bytes=0-524288`: previews
accept at most 512 KiB, with one extra byte to detect oversized sources. Invalid
UTF-8, stale revisions, and denied reads show an unavailable state.

Markdown uses the chat renderer with raw HTML disabled and images disallowed.
Only absolute HTTP(S) links are clickable. Long prose wraps; code and tables
scroll within the bounded preview. The normal chat image policy is unchanged.
PDF sources retain their PDF-only `X-Frame-Options: SAMEORIGIN` header; images
and video retain the existing viewer and transport behavior.

Before releasing this viewer, run the focused backend module
`aichat.tests.test_evidence_media_stream` with `--keepdb`, the frontend unit suite,
and the frontend production typecheck/build. From `src/frontend`, after compiling
the translation catalogs, run:

```bash
yarn compile
yarn playwright test --config playwright.voice.config.ts tests/pages/pui_ui_evidence.spec.ts
```

These browser tests use real components and mocked APIs, not a deployed backend.
They cover Markdown safety, byte limits, malformed encoding, reopening after a
denied read, desktop/mobile reflow, and the existing PDF locator/focus behavior.
Also verify authorized `.md` and `.markdown` sources and PDF/video/image previews
against the candidate revision before promotion. A viewer-only release requires
no migration, re-ingestion, re-embedding, or worker change.

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
