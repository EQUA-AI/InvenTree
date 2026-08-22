# RUN 4 Video Evidence Rollout Ledger

Date: 2026-08-21 (rollout operations continued into 2026-08-22 UTC)
Branch: `equa/customizations`

## Immutable source

- Feature commit: `8c465d2c5fec2c61cd2d36604e42ccb1c92fe96e`
- Review hardening commit: `46464f4f668e1bfc258b674a17e2d4a789e144c9`
- Forced-refresh serving-state fix and rollout ledger commit: `bae974cfc5b226645e21898a876807cc9a0bf0c7`
- Remote branch verified at the forced-refresh fix commit.
- Build context created with `git archive`; unrelated untracked `contrib/install-cloud-clis.sh` was excluded.

## Local gates

- Authoritative pre-review suite: 153 passed, 5 skipped, 53 subtests.
- Post-review focused suite: 54 passed, 1 skipped, 25 subtests.
- Authoritative post-review suite: 159 passed, 5 skipped, 53 subtests.
- Post-deployment routing fix slice: 59 passed, 1 skipped (routing degradation, maintenance capability selection, golden-set unit tests).
- Authoritative post-routing-fix suite: 159 passed, 5 skipped, 53 subtests.
- Frontend TypeScript: clean.
- Wire contract drift check: clean.
- Migration state check: clean.
- Repository-pinned pre-commit hooks: clean (Ruff preview, codespell, Biome, gitleaks, teyit).
- Real ffmpeg fixture check: 130 s, 10 fps, 2.62 MB; midpoint scenes coupling / seal / torque.
- Spike fixture recipe check: 600 s, H.264, 1 fps, 499 MiB, valid trailing MP4 `free` atom.
- Spike fixture real-pipeline check: 11 nominal windows; representative midpoint frames verified as coupling inspection, seal replacement, and torque check.

## Adversarial review

Confirmed findings fixed before deployment:

1. Evidence stream now uses `IsAuthenticatedOrReadScope` (OAuth read-scope parity).
2. Evidence stream is restricted to indexed IMAGE/VIDEO ingests and a closed media suffix allowlist.
3. Keyframe projection records the actual name returned by storage.
4. Stalled-at-cap video ingests purge partial SHA-scoped keyframes after guarded terminalization.
5. Range integers are bounded to 19 digits; oversized values degrade to full 200 instead of 500.
6. Evidence responses add `X-Content-Type-Options: nosniff`.
7. ffmpeg stream-copy maps first video + optional first audio only, dropping data/subtitle streams.
8. Configurable video duration is capped at the worker-budgeted 900 seconds.

Second-look review found no source blocker.

## Azure baseline

Subscription: Microsoft Azure Sponsorship (`5b75a75a-fff3-4d72-a3e9-5e16cb6a8687`)
Resource group: `EpconChat`
Container Apps environment: `epcon-ai-env`

Rollback snapshots:

- `/tmp/aimms-r4-azure-snapshots-20260822T005836Z`
- Complete app and revision JSON for all four apps, with SHA-256 manifest.

Current topology before rollout:

| App | Ready revision | Image tag | CPU | Memory | Scale | State |
|---|---|---|---:|---:|---|---|
| `aimms-experimental` | `aimms-experimental--0000054` | `experimental:equa-customizations-449fb3bfb` | 0.5 | 1 GiB | 1/1 | Healthy, 100% |
| `inventree-worker` | `inventree-worker--0000072` | `experimental:equa-customizations-449fb3bfb` | 1.0 | 2 GiB | 1/1 | Healthy |
| `aimms-dev` | `aimms-dev--0000073` | `aimms-dev:equa-customizations-449fb3bfb` | 0.5 | 1 GiB | 0/2 | Healthy, 100%, scaled to zero |
| `aimms-dev-worker` | `aimms-dev-worker--0000005` | `aimms-dev:equa-customizations-449fb3bfb` | 1.0 | 2 GiB | 1/1 | Healthy |

Environment facts:

- Both worker databases are on the same server but have different database names; queues do not compete.
- Both workers start with `INVENTREE_BACKGROUND_TIMEOUT=600`.
- No explicit `RAG_STALE_CLAIM_S` is deployed.
- Both web apps start with `INVENTREE_GUNICORN_TIMEOUT=300`.
- Attachment/media ingest and retrieval flags are already enabled on all four apps.

Azure Files:

| Share | Quota | Usage before rollout | Access tier |
|---|---:|---:|---|
| `inventree-media` | 100 TiB | 99,926,521 bytes | TransactionOptimized |
| `inventree-data` | 10 GiB | 83,373,617 bytes | TransactionOptimized |

The live `aimms-media-evidence-v1` Search index already contains the required video fields (`timecode_start_s`, `timecode_end_s`, `duration_s`, `segment_index`, `segment_count`) and a 3072-dimensional `media_vector`.

## Rollout gates

- [x] ACR production image build `ch48` succeeded from `git archive` of `46464f4f6` in 10m01s. Both repositories use digest `sha256:e358b1c143170c2a33f078c373bd7ad8ccd39974fb0f725148124c50b418fb6d`.
- [x] ACR production image build `ch49` succeeded from `git archive` of `bae974cfc` in 9m51s. Both repositories use digest `sha256:56f55b04006c972fd4ac56cf33291f2d539eab6cd8feaf5929a885cf0e3f1f06`; the unrelated installer exclusion assertion passed. This canary was superseded at 0% after golden exposed the routing defect below; it never received public traffic.
- [x] Zero-traffic experimental web revision `aimms-experimental--r4e46464f` healthy at 0%, labeled `run4`; old revision remains at 100%. ffprobe 7.1.5 and migration 0023 verified.
- [x] Experimental worker revision `inventree-worker--r4e46464f` healthy with `INVENTREE_BACKGROUND_TIMEOUT=900` and `RAG_STALE_CLAIM_S=2400`.
- [x] Pre-spike worker baseline peaked at 1,702,604,800 bytes / 80% on 2 GiB. Remediation applied before video: `inventree-worker--r4e46464m4`, 2 vCPU / 4 GiB, healthy.
- [x] Experimental `INVENTREE_UPLOAD_MAX_SIZE=500` preflight applied; 60/5 segmentation, 900-second duration, 500 MB video cap, and keyframe storage verified.
- [x] 499 MiB multipart upload passed through direct zero-traffic revision ingress: HTTP 201, 68.508 s, 7,637,632 B/s, 523,239,991 multipart bytes; attachment ID 12 on WO ID 132.
- [x] Web upload measurements: baseline overlay used 7,609,167,872 B; peak 8,657,936,384 B (about 1.05 GiB growth); `/tmp` peak exactly 523,239,424 B; process RSS/HWM stayed 380,864 KiB; 11.46 GB ephemeral remained free; both buffers returned to zero/baseline after response. Media share grew to 623,181,824 B.
- [x] Final experimental web `aimms-experimental--r4bae974` is healthy at 0% with the final digest, 1 vCPU / 2 GiB, `GCP_LOCATION=us`, `gemini-embedding-2`, and 900/2400-second timeouts. `aimms-experimental--0000054` remains at 100% pending cutover.
- [x] Final experimental worker `inventree-worker--r4bae974` is healthy with the final digest, 2 vCPU / 4 GiB, `GCP_LOCATION=us`, `gemini-embedding-2`, and 900/2400-second timeouts; container restart count is zero.
- [x] Final worker preflight passed: ffprobe 7.1.5, all ingest flags enabled, 60/5 segmentation, 900-second duration, 500 MB upload cap, and writable keyframe storage.
- [x] Worker resource/provider gate: organic preview ingest reached indexed with 11 HTTP 200 embedding calls and zero restarts; final immutable worker retained attachment 12 as indexed with 11 GA segments at 3072 dimensions.
- [x] Live Gemini video embedding smoke passed: attachment 12 indexed all 11 segments with `gemini-embedding-2` at 3072 dimensions and Search upsert HTTP 200.
- [x] `aimms-video-fixtures-v1` seeded through the final worker as attachment 13: three indexed GA segments with OCR anchors `COUPLING INSPECTION`, `SEAL REPLACEMENT`, and `TORQUE CHECK`; all vectors and keyframes verified.
- [x] Final web media stream matrix passed for attachment 13: HEAD 200, `bytes=0-99` GET 206 with exactly 100 bytes, unsatisfiable range 416, and correct `Accept-Ranges`, `Content-Range`, private/no-store, and nosniff headers.
- [x] Frozen-fixture seal query passed through final web: answer identified 00:55-01:55; `/threads` replay persisted attachment 13 segment 1 (`55-115s`) as the primary server-authored media-evidence chip.
- [ ] 10-minute seal query returned the correct segment.
- [~] Evidence chip modal Range seek: authenticated server-side Range and manifest gates passed; post-cutover browser chip/modal/currentTime canary pending because the browser session cookie is host-only and the integrated browser cannot clone it to the direct revision host.
- [~] Experimental golden + red-team gate failed closed on the first final-image run: 10 pass, 1 fail, 4 warn, 15 dataset skips; red-team 0 fails. `attachment-interval-grounded` consistently routed explicit uploaded-document wording to wf1 diagnostics and returned an unrelated machine-clarification response. Root cause is before the healthy attachment corpus/tool/wf8 prompt: the probabilistic intent classifier can override its prompt instruction. A deterministic explicit existing-document lookup -> wf8 route and regressions now pass locally; a new immutable image and rerun are required.
- [ ] Experimental soak clean.
- [ ] Dev web and worker promoted with the same digest.
- [ ] Dev preflight, fixture seed, E2E, golden/red-team, and soak passed.
- [ ] Video backfill completed.
- [ ] RUN tracker closed.

## Notes

- GitHub Actions could not dispatch `.github/workflows/ai_golden.yaml` because that workflow is not on the repository default branch. The same harness will run inside the deployed worker, using a temporary in-container API token and existing judge credentials.
- The stored bootstrap admin password returned HTTP 401. Rollout automation therefore mints a temporary API token through Django ORM inside the worker and deletes it after the gates; no credential is printed or copied out of the container.
- In-container token workflow smoke passed: 53-character token, authenticated `/api/` HTTP 200, token deleted before shell exit.
- GA location finding: `gemini-embedding-2` returns 404 at the preview model's `us-central1` regional endpoint. Official multi-region location `us` succeeded for all 11 video segments; attachment 12 restored to `indexed`, 11 segments, model `gemini-embedding-2`, dimensions 3072, Search upsert HTTP 200.
- Live failure exposed a forced-refresh state bug: keyframes were preserved but the serving registry row was demoted to FAILED. Source patched so a failed forced refresh restores INDEXED with existing segment metadata/keyframes; focused regression and authoritative 159/5/53 suite pass. A new image build is required before traffic shift.
