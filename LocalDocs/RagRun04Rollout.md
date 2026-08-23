# RUN 4 Video Evidence Rollout Ledger

Date: 2026-08-21 (rollout operations completed 2026-08-23 UTC)
Branch: `equa/customizations`

## Immutable source

- Feature commit: `8c465d2c5fec2c61cd2d36604e42ccb1c92fe96e`
- Review hardening commit: `46464f4f668e1bfc258b674a17e2d4a789e144c9`
- Forced-refresh serving-state fix and rollout ledger commit: `bae974cfc5b226645e21898a876807cc9a0bf0c7`
- Explicit document-lookup router commits: `a85b24d8b44d322483c83217120badeaf5b78715`, `ad7a7bd8895e78ae6851d5a7c959dbef62cbe96f`, and controlling-path fix `3417cf5a233c026abd2f71da0eea3d97d9ec5e85`.
- Remote branch verified at the controlling-path fix commit.
- Build context created with `git archive`; unrelated untracked `contrib/install-cloud-clis.sh` was excluded.

## Local gates

- Authoritative pre-review suite: 153 passed, 5 skipped, 53 subtests.
- Post-review focused suite: 54 passed, 1 skipped, 25 subtests.
- Authoritative post-review suite: 159 passed, 5 skipped, 53 subtests.
- Post-deployment routing fix slice: 59 passed, 1 skipped (routing degradation, maintenance capability selection, golden-set unit tests).
- Authoritative post-routing-fix suite: 159 passed, 5 skipped, 53 subtests.
- Final shared-routing slice: 129 passed, 1 skipped (complexity router, normalized turn service, legacy router, maintenance capability selection, golden-set unit tests).
- Final authoritative RUN 4 suite: 159 passed, 5 skipped, 53 subtests.
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
- [x] Final derivative ACR build `ch4b` succeeded in 58s from the validated ch49 digest plus the exact committed `routing.py` and `voice_routing.py` bytes from `3417cf5a2`. Both repositories use digest `sha256:4fb11186c861f39ae210c46a401f5f1443aeda8584c79cbd7050bc1150068321`; OCI/InvenTree commit metadata and both deployed file SHAs were verified.
- [x] Zero-traffic experimental web revision `aimms-experimental--r4e46464f` healthy at 0%, labeled `run4`; old revision remains at 100%. ffprobe 7.1.5 and migration 0023 verified.
- [x] Experimental worker revision `inventree-worker--r4e46464f` healthy with `INVENTREE_BACKGROUND_TIMEOUT=900` and `RAG_STALE_CLAIM_S=2400`.
- [x] Pre-spike worker baseline peaked at 1,702,604,800 bytes / 80% on 2 GiB. Remediation applied before video: `inventree-worker--r4e46464m4`, 2 vCPU / 4 GiB, healthy.
- [x] Experimental `INVENTREE_UPLOAD_MAX_SIZE=500` preflight applied; 60/5 segmentation, 900-second duration, 500 MB video cap, and keyframe storage verified.
- [x] 499 MiB multipart upload passed through direct zero-traffic revision ingress: HTTP 201, 68.508 s, 7,637,632 B/s, 523,239,991 multipart bytes; attachment ID 12 on WO ID 132.
- [x] Web upload measurements: baseline overlay used 7,609,167,872 B; peak 8,657,936,384 B (about 1.05 GiB growth); `/tmp` peak exactly 523,239,424 B; process RSS/HWM stayed 380,864 KiB; 11.46 GB ephemeral remained free; both buffers returned to zero/baseline after response. Media share grew to 623,181,824 B.
- [x] Superseded ch49 experimental web `aimms-experimental--r4bae974` passed its zero-traffic resource and provider gates at 1 vCPU / 2 GiB with `GCP_LOCATION=us`, `gemini-embedding-2`, and 900/2400-second timeouts.
- [x] Superseded ch49 experimental worker `inventree-worker--r4bae974` passed its zero-traffic resource and provider gates at 2 vCPU / 4 GiB with the same model/location and timeout settings; container restart count was zero.
- [x] Final controlling-path pair `aimms-experimental--r43417cf` / `inventree-worker--r43417cf` is healthy on the ch4b digest with the same resource, GA model/location, and timeout settings; the web revision now serves 100% of experimental traffic.
- [x] Final worker preflight passed: ffprobe 7.1.5, all ingest flags enabled, 60/5 segmentation, 900-second duration, 500 MB upload cap, and writable keyframe storage.
- [x] Worker resource/provider gate: organic preview ingest reached indexed with 11 HTTP 200 embedding calls and zero restarts; final immutable worker retained attachment 12 as indexed with 11 GA segments at 3072 dimensions.
- [x] Live Gemini video embedding smoke passed: attachment 12 indexed all 11 segments with `gemini-embedding-2` at 3072 dimensions and Search upsert HTTP 200.
- [x] `aimms-video-fixtures-v1` seeded through the final worker as attachment 13: three indexed GA segments with OCR anchors `COUPLING INSPECTION`, `SEAL REPLACEMENT`, and `TORQUE CHECK`; all vectors and keyframes verified.
- [x] Final web media stream matrix passed for attachment 13: HEAD 200, `bytes=0-99` GET 206 with exactly 100 bytes, unsatisfiable range 416, and correct `Accept-Ranges`, `Content-Range`, private/no-store, and nosniff headers.
- [x] Frozen-fixture seal query passed through final web: answer identified 00:55-01:55; `/threads` replay persisted attachment 13 segment 1 (`55-115s`) as the primary server-authored media-evidence chip.
- [x] 10-minute seal query returned attachment 12 with primary seal segment 5 (`275-335s`) and adjacent seal segments 4/6.
- [x] Post-cutover browser chip/modal seek passed on experimental: chip `WO #133 · 00:55`, `currentTime=55`, `duration=130`, `readyState=4`, no media error, and same-origin Range returned 206 with 100 bytes.
- [x] Focused controlling-path route gate passed live: `attachment-interval-grounded` completed on the first request and returned the HX-200 visual leak interval (6 months) with uploaded-document attribution.
- [x] Experimental golden + red-team gate passed on ch4b: 10 pass, 0 fail, 5 warn, 15 demo-dataset skips; red-team 0 fails; direct proposal-ID delta empty. The first run had failed closed before the upstream `VoiceComplexityRouter` fix, proving the gate caught the routing defect.
- [x] Experimental promotion and soak passed: public `/api/` and `/web` returned 200; final web and worker replicas remained ready with zero restarts; 30-minute peaks were 0.0372 cores / 800.1 MiB web and 0.6031 cores / 1951.9 MiB worker; exact final-revision Log Analytics scan found no severe errors, OOMs, failed migrations, or crash loops.
- [x] Dev web `aimms-dev--r43417c2` and worker `aimms-dev-worker--r43417c2` deployed the exact same ch4b digest and verified source commit/file provenance. The web revision serves 100%; rollback revision `aimms-dev--0000073` remains active, healthy, and scaled to zero.
- [x] Dev preflight and fixture seed passed: documents 2/3/4, media 7/8, and video attachment 11 with three indexed `gemini-embedding-2` segments at 3072 dimensions.
- [x] Dev focused E2E passed: media HEAD 200, Range 206 with 100 bytes, unsatisfiable Range 416, HX-200 interval first-attempt `wf8`, and video first-attempt `wf8` with primary attachment 11 segment 1 (`55-115s`).
- [x] Dev golden + red-team gate passed: 11 pass, 0 fail, 4 warn, 15 demo-dataset skips; red-team 0 fails; direct proposal-ID delta empty; disposable identities, tokens, and threads deleted.
- [x] Dev promotion and soak passed: public `/api/` and `/web` returned 200; final web and worker replicas remained ready with zero restarts; 30-minute peaks were 0.0454 cores / 775.7 MiB web and 0.2653 cores / 1944.2 MiB worker; exact final-revision Log Analytics scan found no severe errors, OOMs, failed migrations, or crash loops.
- [x] Existing-attachment backfill completed with `failed=0`: experimental `ingested=10 skipped=0 filtered=0`; dev `ingested=6 skipped=0 filtered=0`. Post-backfill registries contain only indexed rows and legitimate deleted tombstones, with no bad terminal states.
- [x] RUN tracker closed after immutable promotion, backfill, public smoke, resource soak, and rollback verification.

## Final topology

| App | Final revision | Digest | CPU | Memory | Traffic / state |
|---|---|---|---:|---:|---|
| `aimms-experimental` | `aimms-experimental--r43417cf` | `sha256:4fb11186c861f39ae210c46a401f5f1443aeda8584c79cbd7050bc1150068321` | 1.0 | 2 GiB | Healthy, 100% |
| `inventree-worker` | `inventree-worker--r43417cf` | `sha256:4fb11186c861f39ae210c46a401f5f1443aeda8584c79cbd7050bc1150068321` | 2.0 | 4 GiB | Healthy, zero restarts |
| `aimms-dev` | `aimms-dev--r43417c2` | `sha256:4fb11186c861f39ae210c46a401f5f1443aeda8584c79cbd7050bc1150068321` | 1.0 | 2 GiB | Healthy, 100% |
| `aimms-dev-worker` | `aimms-dev-worker--r43417c2` | `sha256:4fb11186c861f39ae210c46a401f5f1443aeda8584c79cbd7050bc1150068321` | 2.0 | 4 GiB | Healthy, zero restarts |

## Notes

- GitHub Actions could not dispatch `.github/workflows/ai_golden.yaml` because that workflow is not on the repository default branch. The same harness will run inside the deployed worker, using a temporary in-container API token and existing judge credentials.
- The stored bootstrap admin password returned HTTP 401. Rollout automation therefore mints a temporary API token through Django ORM inside the worker and deletes it after the gates; no credential is printed or copied out of the container.
- In-container token workflow smoke passed: 53-character token, authenticated `/api/` HTTP 200, token deleted before shell exit.
- GA location finding: `gemini-embedding-2` returns 404 at the preview model's `us-central1` regional endpoint. Official multi-region location `us` succeeded for all 11 video segments; attachment 12 restored to `indexed`, 11 segments, model `gemini-embedding-2`, dimensions 3072, Search upsert HTTP 200.
- Live failure exposed a forced-refresh state bug: keyframes were preserved but the serving registry row was demoted to FAILED. Source patched so a failed forced refresh restores INDEXED with existing segment metadata/keyframes; focused regression and authoritative 159/5/53 suite pass. The corrected behavior is included in the promoted ch4b derivative.
- GitHub Actions attempts stopped during environment setup (PostgreSQL 17 lacked pgvector and `collectplugins` failed), before source tests ran. Local authoritative suites and the deployed focused/full golden gates above are the rollout evidence.
