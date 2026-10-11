# Azure Deployment Plan

> **Status:** Deployed

Generated: 2026-07-16T22:07:23Z

## 1. Project Overview

**Goal:** Build the committed `equa/customizations` source, push it to the existing Azure Container Registry repository `experimental`, and deploy that image to the existing Container App `aimms-experimental`.

**Path:** Update an existing experimental deployment. No infrastructure provisioning or configuration replacement.

## 2. Requirements

| Attribute | Value |
|-----------|-------|
| Classification | Experimental |
| Scale | Existing 0-2 replicas, 0.5 CPU, 1 GiB per replica |
| Budget | Existing Consumption workload profile |
| Subscription | Microsoft Azure Sponsorship (`5b75a75a-fff3-4d72-a3e9-5e16cb6a8687`) |
| Location | East US 2 |
| Resource group | `EpconChat` |

The user explicitly requested this rollout. The target-confirmation prompt returned an instruction to proceed autonomously with the existing resources.

## 3. Components Detected

| Component | Type | Technology | Path |
|-----------|------|------------|------|
| InvenTree / AIMMS | Web API and SPA | Django, React, Gunicorn/Uvicorn | Repository root |
| Container image | OCI image | Multi-stage Dockerfile | `contrib/container/Dockerfile` |
| Machine demo extension | Django command and manifest | Python / JSON | `src/backend/InvenTree/assets/` |

## 4. Recipe Selection

**Selected:** Azure CLI (`az acr build` + `az containerapp update`)

**Rationale:** Both ACR and Container App already exist and are configured. The rollout changes only the image, preserving environment, scaling, ingress, identities, secrets, and volumes.

## 5. Architecture

| Component | Azure Service | Existing Configuration |
|-----------|---------------|------------------------|
| Image repository | ACR `aimms` / repository `experimental` | Standard, East US 2 |
| Web application | Container App `aimms-experimental` | Multiple revisions, external port 8000 |
| Image pull | System-managed identity | `AcrPull` scoped to ACR `aimms` |

## 6. Provisioning Limit Checklist

| Resource Type | Number to Deploy | Total After Deployment | Limit/Quota | Notes |
|---------------|------------------|------------------------|-------------|-------|
| Azure resources | 0 | Unchanged | Not applicable | Image/revision rollout only; existing 0-2 replica scale envelope is unchanged |

`az quota` could not query because `Microsoft.Quota` is not registered and the local quota extension has a missing `rpds` module. This does not block a zero-provisioning image update. No provider registration was performed.

## 7. Artifact and Rollback

- Git commit: `6b56791f15b94a2126aa69bb79d5911dbc9199e4`
- GitHub branch: `origin/equa/customizations`
- Build source: public Git context pinned to the exact full commit
- Target image: `aimms-hjcxb6epgvhgbyge.azurecr.io/experimental:equa-customizations-6b56791f15b9`
- Target digest: `sha256:d909b8693c805b4964c13b835256a6721feb150c865ad7a9ac0896baacfea8ce`
- Current revision: `aimms-experimental--0000003`
- Previous image: `aimms-hjcxb6epgvhgbyge.azurecr.io/experimental:equa-customizations-dfea901f9275`
- Previous ready revision: `aimms-experimental--0000002`

If the new revision is unhealthy, restore the previous image with `az containerapp update` and verify the restored revision and API endpoint before ending the rollout.

## 8. Execution Checklist

- [x] Verify Azure CLI account and target resources
- [x] Verify ACR authentication and repository
- [x] Verify system identity `AcrPull` role
- [x] Verify Container Apps environment and current rollback revision
- [x] Verify GitHub contains the exact commit
- [x] Validate clean committed build context and Dockerfile
- [x] Build and push initial immutable image to ACR
- [x] Confirm initial image manifest and digest
- [x] Update `aimms-experimental` to the initial immutable image
- [x] Confirm revision `aimms-experimental--0000002` is healthy and receives traffic
- [x] Confirm failed data load rolled back transactionally at 6 / 16 / 6
- [x] Commit and push revision-exact cleanup fix (`6b56791f15b9`)
- [x] Build and push final corrected immutable image to ACR
- [x] Update `aimms-experimental` to final corrected image
- [x] Confirm revision `aimms-experimental--0000003` is healthy and receives traffic
- [x] Run idempotent machine demo loader in the new revision
- [x] Verify second loader run removes zero rows
- [x] Verify machine, installed-part, maintenance, and work-order counts
- [x] Verify authenticated Machines API
- [x] Verify public API and frontend after deployment

## 9. Validation Proof

| Check | Command / Evidence | Result | Timestamp |
|-------|--------------------|--------|-----------|
| GitHub commit | `git ls-remote origin refs/heads/equa/customizations` | Pass: `dfea901f9275...` | 2026-07-16T22:07:23Z |
| Clean context | Detached Git worktree and `git status --short` | Pass: exact commit, clean | 2026-07-16T22:07:23Z |
| Dockerfile | `docker build --check --target production ...` | Pass: no warnings | 2026-07-16T22:07:23Z |
| ACR connectivity | `az acr check-health --name aimms` | Pass: Docker, DNS, challenge, refresh/access tokens; optional Helm check unavailable | 2026-07-16T22:07:23Z |
| ACR pull RBAC | Role assignments for Container App principal on ACR | Pass: `AcrPull` | 2026-07-16T22:07:23Z |
| Resource state | Resource group, environment, ACR, and app queries | Pass: all `Succeeded`; app `Running` | 2026-07-16T22:07:23Z |
| Immutable tag | ACR tag query | Pass: target tag unused | 2026-07-16T22:07:23Z |
| API baseline | `GET https://aimms-experimental.kindpebble-bfe407e4.eastus2.azurecontainerapps.io/api/` | Pass: HTTP 200, API version 423 | 2026-07-16T22:07:23Z |
| Container exec | `az containerapp exec ... --command 'python --version'` | Pass: Python 3.11.13 in ready replica | 2026-07-16T22:17:55Z |
| Demo-data baseline | Read-only Django ORM count in current revision | Pass: 6 machines / 16 links / 6 history rows | 2026-07-16T22:17:55Z |
| Initial image build | ACR Quick Run `ch1e` | Pass: `experimental:equa-customizations-dfea901f9275`, digest `sha256:a53498468f5c...c06b` | 2026-07-16T22:43:06Z |
| Initial revision | Container App revision and replica queries | Pass: `aimms-experimental--0000002` Healthy, ready, zero restarts, 100% traffic | 2026-07-16T22:48:30Z |
| Loader rollback | `load_asset_demo_data --prune` plus read-only ORM recount | Pass: ambiguity raised before commit; database remained 6 / 16 / 6 | 2026-07-16T22:49:17Z |
| Cleanup fix | GitHub `origin/equa/customizations` | Pass: `6b56791f15b94a2126aa69bb79d5911dbc9199e4` | 2026-07-16T22:53:20Z |
| Provenance retry | ACR Quick Run `ch1f` | Canceled before push after detecting inaccurate informational commit timestamp | 2026-07-16T22:58:03Z |
| Final image build | ACR Quick Run `ch1g` | Pass: exact Git head `6b56791f15b9...`, digest `sha256:d909b8693c80...a8ce` | 2026-07-16T23:08:16Z |
| Final revision | Container App app/revision/replica queries | Pass: `aimms-experimental--0000003` Healthy, ready, zero restarts, 100% traffic | 2026-07-16T23:11:10Z |
| Production load | `load_asset_demo_data --prune` | Pass: 6 machines, 31 links, 24 history rows, 18 work orders; removed 18 legacy rows | 2026-07-16T23:11:39Z |
| Idempotency | Second `load_asset_demo_data --prune` | Pass: same counts, removed 0 rows | 2026-07-16T23:13:32Z |
| Production ORM | Aggregate and per-machine count query | Pass: every machine has 4 history rows and at least 5 links; zero placeholders | 2026-07-16T23:14:20Z |
| Machines API | Authenticated `GET /api/assets/machines/?limit=20` | Pass: 6 enriched machines and ACME customer linkage | 2026-07-16T23:17:30Z |
| Public API | `GET /api/` | Pass: HTTP 200, API version 519 | 2026-07-16T23:16:00Z |
| Frontend | Follow redirect from `/` | Pass: HTTP 200 at `/web` | 2026-07-16T23:18:38Z |

**Validated by:** GitHub Copilot following the Azure validation workflow

## 10. Files

No infrastructure or application configuration files are generated. This plan is local deployment evidence and is ignored by Git.

---

## 11. Rollout Update — 2026-07-17 (Voice Live gateway wiring)

Follow-up image rollout using the same recipe (`az acr build` + `az containerapp update`).

| Attribute | Value |
|-----------|-------|
| Git commit | `5671e21964dd84c485f57b8d56a67ef43a3cc9bc` (local `equa/customizations`; **not yet pushed to GitHub** — no credentials available in the build environment) |
| Change | Voice Live provider gateway (WS4-T4): SDP channel factory, exact-TTS dispatch, lifespan wiring, `azure-ai-projects` dependency |
| Build source | `git archive` of the exact commit, staged as a tarball in `epcon0ai0storage/acr-build-ctx` (direct `az acr build` context upload timed out twice on the local uplink); blob deleted after the build |
| ACR run | `ch1h`, succeeded in 8m31s |
| Image | `aimms-hjcxb6epgvhgbyge.azurecr.io/experimental:equa-customizations-5671e21964dd` |
| Digest | `sha256:5e7cee9155ed691fbebbec2979933f0827352bebff33498b28b92de53e4d9c67` |
| Revision | `aimms-experimental--0000004` — Healthy, Running, 100% traffic |
| Previous revision | `aimms-experimental--0000003` (image `equa-customizations-6b56791f15b9`) for rollback |
| Verification | `/api/` 200 · `/api/ai/voice/capability` 401 (route live, auth required) · `/` → `/web` 200 · `gateway.py` present in container · `azure-ai-projects` 2.3.0 installed |
| Outstanding | Push commit `5671e2196` to `origin/equa/customizations`; grant `Cognitive Services User` on `AIMMS-Foundry` to principal `d5213280-ea28-484a-8f3f-10ff8febea35` |

---

## 12. Planned Rollout — 2026-07-24 (AI voice parity and latency)

> **Status:** Deployed successfully with an explicit experimental CI waiver.

### 12.1 Fixed release inputs

| Attribute | Planned value |
|-----------|---------------|
| GitHub repository | `EQUA-AI/InvenTree` |
| GitHub branch | `equa/customizations` |
| Exact source commit | `7779b5720d8edfcbb9c5b944845572059ce314da` |
| Commit verification | GitHub reports `unsigned` / not cryptographically verified |
| Commit URL | `https://github.com/EQUA-AI/InvenTree/commit/7779b5720d8edfcbb9c5b944845572059ce314da` |
| Commit time | `2026-07-24T02:13:15Z` |
| ACR | `aimms-hjcxb6epgvhgbyge.azurecr.io` (`aimms`, Standard, East US 2) |
| ACR repository | `experimental` |
| Candidate tag | `equa-customizations-7779b5720-20260724030731` |
| Candidate image | `aimms-hjcxb6epgvhgbyge.azurecr.io/experimental:equa-customizations-7779b5720-20260724030731` |
| Container App | `aimms-experimental` in `EpconChat` |
| Candidate suffix / revision | `c7779b5720p` / `aimms-experimental--c7779b5720p` |
| Current production revision | `aimms-experimental--0000016` (Healthy, Running, 100% traffic) |
| Current rollback image | `aimms-hjcxb6epgvhgbyge.azurecr.io/experimental:equa-customizations-19e42d421-20260723033138` |
| Current rollback digest | `sha256:403860589685b1879db9cd7edfbf0522db420b4ebf3aaec697c465fba3c95f3f` |

The candidate tag and revision suffix were confirmed unused. The GitHub branch tip and local `HEAD` both resolve to the exact source commit. The local dirty scheduling/frontend work was excluded because ACR built from the GitHub URL pinned to that commit.

### 12.2 Current-state checks

- Azure subscription: Microsoft Azure Sponsorship (`5b75a75a-fff3-4d72-a3e9-5e16cb6a8687`).
- Container App: provisioning `Succeeded`, running `Running`, multiple-revision mode, 0.5 CPU / 1 GiB, 1-2 replicas, external port 8000.
- Production baseline: `/api/` 200, `/` -> `/web` 200, unauthenticated `/api/ai/voice/capability` 401 as expected.
- The system-assigned Container App identity has `AcrPull` at the `aimms` registry scope.
- Registry challenge and access-token checks pass. Local Docker and Helm are unavailable, but ACR Quick Build does not require either locally.
- No ACR Task, ACR webhook, or GitHub Actions workflow deploys `aimms-experimental`; the proven path remains an explicit GitHub-pinned `az acr build` followed by Container App revision promotion.
- No migrations, Dockerfile changes, backend dependency changes, or workflow changes exist between deployed source `19e42d421` and candidate `7779b5720`. The delta does include frontend Markdown dependencies and must compile inside the production image build.
- ACR repository tags are write-enabled. The unique tag is retained for discovery, but the Container App revision must use the resolved digest reference so a later tag overwrite cannot change the deployed artifact.

### 12.3 Approval Gate 0 — CI disposition

GitHub checks for the candidate SHA are not all green. The same failed job names also occur on the currently deployed source SHA, so they are inherited branch-wide failures rather than new ACR failures:

- Import/export and browser setup fail while loading a demo fixture containing removed field `ParameterTemplate.unique`.
- API schema setup fails because its environment lacks `psycopg` / `psycopg2`.
- Full-repository `prek` fails on pre-existing formatting and `unused-async` findings outside the committed AI file set.

Candidate-specific evidence that is green:

- GitHub frontend `Build` succeeds.
- Local commit hooks pass for the committed file set.
- AI core suite: 536 passed, 7 opt-in live integrations skipped.
- Live Azure A/B tests for text, voice transcript, and capture-only RFQ proposal paths passed before commit.

Choose exactly one before Gate 1:

1. **Strict (recommended):** remediate/rerun the failing GitHub jobs and require green checks.
2. **Experimental waiver:** explicitly accept the inherited CI failures for this experimental app and rely on immutable build provenance, zero-traffic candidate tests, 10% canary, and immediate traffic rollback.

### 12.4 Approval Gate 1 — build immutable ACR artifact

Run only after Gate 0 is resolved:

```bash
SUBSCRIPTION_ID='5b75a75a-fff3-4d72-a3e9-5e16cb6a8687'
RG='EpconChat'
ACR='aimms'
REPOSITORY='experimental'
APP='aimms-experimental'
CONTAINER='aimms-experimental'
SOURCE_SHA='7779b5720d8edfcbb9c5b944845572059ce314da'
SOURCE_DATE='2026-07-24T02:13:15Z'
TAG='equa-customizations-7779b5720-20260724030731'
IMAGE="aimms-hjcxb6epgvhgbyge.azurecr.io/${REPOSITORY}:${TAG}"
SOURCE_URL="https://github.com/EQUA-AI/InvenTree.git#${SOURCE_SHA}"

az account set --subscription "$SUBSCRIPTION_ID"

az acr build \
	--resource-group "$RG" \
	--registry "$ACR" \
	--image "${REPOSITORY}:${TAG}" \
	--file contrib/container/Dockerfile \
	--target production \
	--platform linux/amd64 \
	--build-arg "commit_tag=${TAG}" \
	--build-arg "commit_hash=${SOURCE_SHA}" \
	--build-arg "commit_date=${SOURCE_DATE}" \
	"$SOURCE_URL"
```

Build acceptance:

- ACR run succeeds.
- The previously unused unique tag resolves to a digest.
- Manifest labels contain the exact `org.opencontainers.image.revision` source SHA.
- Production image starts sufficiently to run `invoke version` in a disposable context or candidate revision.
- Do not overwrite or add a mutable `latest` tag.

Record before proceeding:

```bash
CANDIDATE_DIGEST=$(az acr repository show \
	--name "$ACR" \
	--image "${REPOSITORY}:${TAG}" \
	--query digest -o tsv)
IMAGE_BY_DIGEST="aimms-hjcxb6epgvhgbyge.azurecr.io/${REPOSITORY}@${CANDIDATE_DIGEST}"
printf 'candidate tag=%s\ncandidate digest=%s\ndeploy reference=%s\n' \
	"$IMAGE" "$CANDIDATE_DIGEST" "$IMAGE_BY_DIGEST"
```

### 12.5 Approval Gate 2 — create and test zero-production-traffic revision

Create the candidate by copying the current ready template and changing only the image and revision suffix. This preserves all environment variables, secret references, volume mounts, identity, scaling, ingress, and probes.

```bash
CURRENT_REV='aimms-experimental--0000016'
CANDIDATE_SUFFIX='c7779b5720'
CANDIDATE_REV="${APP}--${CANDIDATE_SUFFIX}"

az containerapp revision copy \
	--resource-group "$RG" \
	--name "$APP" \
	--from-revision "$CURRENT_REV" \
	--container-name "$CONTAINER" \
	--image "$IMAGE_BY_DIGEST" \
	--revision-suffix "$CANDIDATE_SUFFIX" \
	--output none
```

Immediately verify the application FQDN still routes 100% to `aimms-experimental--0000016`. If it does not, run the rollback command in section 12.8 before any other action.

Wait for candidate status `Provisioned`, `Healthy`, `Running`, with at least one ready replica. The revision-specific FQDN is directly reachable without a label, so candidate smoke testing does not require production traffic or application-scope label changes:

```bash
CANDIDATE_FQDN=$(az containerapp revision show \
	--resource-group "$RG" \
	--name "$APP" \
	--revision "$CANDIDATE_REV" \
	--query properties.fqdn -o tsv)

curl --fail --silent --show-error "https://${CANDIDATE_FQDN}/api/" >/dev/null
curl --fail --location --silent --show-error "https://${CANDIDATE_FQDN}/" >/dev/null
test "$(curl --silent --output /dev/null --write-out '%{http_code}' \
	"https://${CANDIDATE_FQDN}/api/ai/voice/capability")" = '401'
```

Candidate acceptance:

- Revision image and ACR digest match the Gate 1 artifact.
- `/api/` and `/web` return 200; unauthenticated voice capability returns expected 401.
- Startup logs contain no migration, import, authentication, or AI configuration error.
- `INVENTREE_COMMIT_HASH` in the candidate equals the exact source SHA.
- Existing secret references, `inventree-media-vol`, and `/home/inventree/data/media` mount match the current revision.
- Authenticated manual smoke tests pass:
	- Text conversational response and Kanban lookup.
	- Voice lookup with the same user/RBAC behavior as text.
	- Complete voice RFQ request produces a proposal; say **cancel** and verify no RFQ/email effect occurs.
	- A user missing the required role cannot propose or execute the action.

### 12.6 Approval Gate 3 — 10% canary

Only after Gate 2 approval:

```bash
az containerapp ingress traffic set \
	--resource-group "$RG" \
	--name "$APP" \
	--revision-weight \
		"${CURRENT_REV}=90" \
		"${CANDIDATE_REV}=10"
```

Canary observation window: at least 10 minutes and enough manual requests to exercise API, web, text AI, voice lookup, and canceled voice proposal paths. Compare candidate logs with the baseline. Abort on any startup failure, readiness loss, unexpected 4xx/5xx increase, authorization regression, duplicate side effect, or material latency regression.

### 12.7 Approval Gate 4 — production promotion

Only after canary approval:

```bash
az containerapp ingress traffic set \
	--resource-group "$RG" \
	--name "$APP" \
	--revision-weight \
		"${CURRENT_REV}=0" \
		"${CANDIDATE_REV}=100"
```

Post-promotion acceptance:

- Candidate remains Healthy / Running and serves 100% traffic.
- Public API, web, text AI, voice lookup, and canceled voice proposal checks pass again.
- No new error pattern appears in logs.
- Record final revision, image tag, digest, ACR run ID, validation timestamps, and CI disposition in this document.
- Keep `aimms-experimental--0000016` active at 0% for at least 24 hours as the immediate rollback target. Deactivate older zero-traffic revisions only after the soak period.

### 12.8 Immediate rollback / abort

At any Gate 2-4 failure:

```bash
az containerapp ingress traffic set \
	--resource-group "$RG" \
	--name "$APP" \
	--revision-weight \
		'aimms-experimental--0000016=100' \
		'aimms-experimental--c7779b5720=0'
```

Then verify production `/api/` and `/web`, inspect both revisions' logs, and leave the candidate at 0% until the failure is understood. If the candidate never became healthy, deactivate it after collecting diagnostics. Do not delete the current rollback ACR tag or digest.

### 12.9 Required human confirmation

Before any Azure write operation, confirm all of the following:

- [ ] Source SHA `7779b5720d8edfcbb9c5b944845572059ce314da` is the intended release.
- [ ] Local uncommitted scheduling/frontend work must remain excluded.
- [ ] CI choice is explicit: **strict green checks** or **experimental waiver**.
- [ ] Immutable candidate tag `equa-customizations-7779b5720-20260724030731` is acceptable.
- [ ] Zero-traffic candidate -> 10% canary -> 100% promotion is acceptable.
- [ ] Current revision `aimms-experimental--0000016` is the approved rollback target.
- [ ] No database/demo-data loader will be run as part of this image-only rollout.

Suggested approval text:

> Approve Gate 1 for SHA `7779b5720d8edfcbb9c5b944845572059ce314da` using the **strict** CI gate.

or, for an explicit experimental exception:

> Approve Gate 1 for SHA `7779b5720d8edfcbb9c5b944845572059ce314da` with an **experimental CI waiver**. Build the immutable ACR image only; stop again before creating the Container App revision.

### 12.10 Deployment evidence — completed 2026-07-24

The user approved proceeding with the Container App update. The rollout used the experimental CI waiver described in Gate 0.

| Check | Result |
|-------|--------|
| GitHub source | Exact remote branch SHA `7779b5720d8edfcbb9c5b944845572059ce314da` |
| ACR build | Run `ch1q`, Succeeded, `2026-07-24T03:30:36Z` to `03:39:07Z` |
| ACR tag | `experimental:equa-customizations-7779b5720-20260724030731` |
| ACR digest | `sha256:54213f0e856a1d583847a30728e5fcc335b17c683d549172eb49ebaf15b46e25` |
| Deployed image | `aimms-hjcxb6epgvhgbyge.azurecr.io/experimental@sha256:54213f0e856a1d583847a30728e5fcc335b17c683d549172eb49ebaf15b46e25` |
| Production revision | `aimms-experimental--c7779b5720p`, Healthy, Running, 100% traffic |
| Rollback revision | `aimms-experimental--0000016`, retained Active / Healthy / Running at 0% |
| Final app state | Provisioning `Succeeded`, running `Running`, latest ready revision is candidate |
| Final public checks | `/api/` 200, `/` -> `/web` 200, unauthenticated voice capability 401 |
| Final timestamp | `2026-07-24T05:16:22Z` |

#### Provenance correction

The first digest-pinned revision, `aimms-experimental--c7779b5720`, was healthy at zero traffic but failed the provenance gate because `INVENTREE_COMMIT_HASH` and `INVENTREE_COMMIT_DATE` were blank. The repository Dockerfile declares those ARGs globally but does not redeclare them in the `production` stage before assigning them to `ENV`. The ACR log independently proved its Git source checkout was the exact candidate SHA.

A replacement revision, `aimms-experimental--c7779b5720p`, reused the same verified digest and added only these non-secret revision environment values:

- `INVENTREE_COMMIT_HASH=7779b5720d8edfcbb9c5b944845572059ce314da`
- `INVENTREE_COMMIT_DATE=2026-07-24T02:13:15Z`

The original blank-provenance candidate was deactivated before canary traffic.

#### Candidate and RBAC validation

- Candidate revision-specific API and web endpoints returned 200; unauthenticated voice capability returned 401.
- Candidate template retained production secret references, `inventree-media-vol`, and `/home/inventree/data/media` mount.
- Candidate was Healthy / Running / ready with one replica and zero restarts.
- Authenticated voice capability returned enabled with WebRTC enabled.
- Authenticated typed chat completed successfully.
- Authenticated voice Kanban lookup completed through `wf8`.
- Complete RFQ voice request produced `voice_write_propose`; the next turn said `cancel` and returned `Cancelled. No change was made.` No execution workflow was recorded.
- A non-superuser without `aimms.email.send` received `advisory_intent`, not a proposal.

#### Canary and promotion history

1. First 10% canary ran for more than 10 minutes. It completed 210 public request checks with zero status failures, stable latency, Healthy / Running revisions, and zero candidate restarts.
2. First promotion to 100% was immediately rolled back because one authenticated text smoke turn returned `Unable to complete lookup.` Production was restored to `aimms-experimental--0000016=100` before diagnosis.
3. The failure was non-reproducible: five immediate retries, ten additional text turns, and five Kanban tool turns all passed. Candidate logs contained exactly one `T1 lookup failed` event at `2026-07-24T04:43:31Z` and no recurrence.
4. A fresh second 10% canary started at `2026-07-24T04:58:32Z` and ran for 10 minutes 17 seconds. It completed 150 public checks and five authenticated AI checks with zero failures; both revisions remained healthy and candidate restarts remained zero.
5. Second promotion moved candidate to 100%. Post-promotion validation completed 60 public checks and ten authenticated text stability checks with zero failures. The candidate lookup failure count remained at the original single transient event.

#### Final rollback command

```bash
az containerapp ingress traffic set \
	--resource-group EpconChat \
	--name aimms-experimental \
	--revision-weight \
		aimms-experimental--0000016=100 \
		aimms-experimental--c7779b5720p=0
```

Keep `aimms-experimental--0000016` active at 0% through at least `2026-07-25T05:16:22Z`.

---

## 13. Prepared Rollout — 2026-10-02 (pump-station monitoring: Performance tab, limits, alarms, data-quality rules)

> **Status:** built and rehearsed locally; **nothing below has been run against
> Azure.** Re-checked 2026-10-02: the signed-in account `Aniket@equa.work` is
> still denied `Microsoft.App/containerApps/read` on `EpconChat` and cannot see
> the registry. This section replaces the 2026-09-26 preparation, which was
> pinned to `37e31e8e9877` and carried four errors that would have stopped it
> or misdirected it; they are listed in 13.5 so nobody restores them.

### 13.0 Where this image may go — read this before anything else

**Not onto a database that `equa/customizations` has migrated.** The two lines
split at `c54dfbdef` on 2026-08-07 and have not been merged since:

| | this line (`inventTree-aniket`, `IOT`) | `equa/customizations` |
|---|---|---|
| Commits since the split | 132 | 484 |
| Migration files the other line does not have | 11 — `assets` 10, `part` 1 | 91 across 13 apps — 78 new, and 13 squashes of older history that came in with the upstream merge its `merge_upstream_20260912` migrations record |
| `assets` history from 0011 | `0011_equipment_registry` … `0020_state_value_changed_at` | `0011_assetmachine_profile` … `0016_assetmachine_placement_version_and_more` |
| Pumphouse connector | yes | **no** — `machine_health/connectors/` holds `base.py` and `webhook.py` only |

Seven migration numbers collide — `assets` 0011 through 0016 and `part` 0155 —
the same number naming a different migration on each side. An image built from
this line and pointed at a database on the other would run code that knows
nothing of those migrations, and `migrate` would apply its own `assets`
0011–0020 beside a different 0011–0016: Django records migrations by name and
does not object to names it has never heard of. It would also take 484 commits
of other people's work out of whatever it replaced.

The start script now refuses to do that (13.2), and the refusal was rehearsed
against a database carrying the other line's 91 names. That is a backstop for a
mistake, not a route. Reaching the environment that serves
`equa/customizations` is a **merge, not a deploy**: the two branches
reconciled, a merge migration for `assets` and `part`, and a rehearsal against
a copy of that database. None of that is prepared here, and nothing in this
section should be read as doing it.

**Pre-flight, on the target app, before any other command.** Read the table,
not `showmigrations`: that command lists the migrations the *running image* has
files for, so it cannot show a row its own code has never heard of.

```bash
az containerapp exec -n "$APP" -g "$RG" --command /bin/bash
# then, inside the container:
python src/backend/InvenTree/manage.py dbshell
select name from django_migrations where app = 'assets' order by id;
```

| The list | Meaning |
|---|---|
| reaches `0011_equipment_registry` or beyond | this line — proceed |
| contains `0011_assetmachine_profile` | the other line — **stop** |
| ends at `0010_remove_assetmachine_customer` | predates the split — proceed; the rollout takes it forward on this line, and the database is then committed to it |

An app scaled to zero has no replica to open a shell in; load its URL once
first.

**Which Container App.** The repository describes two, and the earlier
preparation chose between them without saying why:

| | `aimms-dev` | `aimms-experimental` |
|---|---|---|
| Recorded in | `app.yaml`, exported 2026-07-10 | sections 1–12 |
| Database | `inventree` on `epconchat-pg-dev.postgres.database.azure.com` | not recorded in this repository |
| `INVENTREE_DEBUG` | `True` | — |
| Revision mode | Single | Multiple, with the gated rollout of 12.5–12.7 |
| Size and scale, as exported | 0.25 vCPU, 0.5 GiB, 0 to 2 replicas | — |
| Probes, as exported | none defined; Microsoft documents defaults for an app with ingress (13.2) | — |
| Registry repository | `aimms-dev`, on the mutable tag `latest` in July | `experimental`, immutable tags |
| Built from | not recorded | `equa/customizations` |
| Cosmos data-plane grant on its identity, 2026-10-02 | **none** | Data Reader on `/dbs/aimms` |

`aimms-experimental` is what sections 1–12 call production, and it is built from
the other line: **it is not a target for this image.** `aimms-dev` is the
candidate, subject to the pre-flight above — its database's history is not
recorded anywhere in this repository and has to be read from the running app.

`worker-revision.yaml` is **not** `aimms-dev`'s worker. It points at database
`postgres` on `machine-ai-chat.postgres.database.azure.com` with site URL
`https://aimms.equa.work/` — a different database from the one in `app.yaml`.
Identify the worker that shares the target's database before updating anything
(13.2 step 0). If the environment has no worker, nothing polls:
`poll_cosmos_pumphouse_sources` is a scheduled task and runs only under
`invoke worker`, so the bootstrap in section 14 would complete and every tile
would stay empty.

| Attribute | Value |
|-----------|-------|
| Git commit | `11cec10e58c04d4eea065b6bcb7a414a130d4f9c` on `origin/inventTree-aniket`. The commit that follows it on the branch changes only this file. |
| Change since the September pin | poller evaluates thresholds; limits applied from a reviewed file with detector voting; a condition closes on sustained recovery; converter rails and over-range readings marked unusable and named on the mimic; time-since-last-change recorded per reading; migrations applied by the start script, behind a lineage check |
| Migrations on this line | `assets.0011` … `assets.0020` and `part.0155_discharge_rate_note`. `0011`–`0014`, `0019` and `0020` change the schema. `0015` registers one unit, `cusec`. `0016`–`0018` and `part.0155` correct pump-station records, which a database not yet bootstrapped does not hold, so they change nothing there. The rollout prints what it is about to apply before it applies it (13.2 step 2). |
| Registry | `aimms` — login server `aimms-hjcxb6epgvhgbyge.azurecr.io` |
| Image | `aimms-dev:inventree-aniket-11cec10e58c0`, deployed by digest |

**Rehearsed before this rollout, locally.** Backend suites `assets` and
`machine_health` green at 592 tests. The three frontend steps the image runs —
`lingui extract`, `lingui compile`, `tsc && vite build` — all exit 0.
The production image was built locally from the pinned commit with the same
Dockerfile, target and build arguments ACR will be given — on `arm64`, where
ACR builds `amd64` — and holds nine files under `contrib/cosmos/review`, one
under `contrib/cosmos/limits`, the frontend bundle, `psql`, and an importable
`azure-cosmos` 4.17.0. `/api/` and `/` both answer 200 from it.

The database step was rehearsed through the real start script against a
throwaway copy of the local database, never the database itself:

| Copy prepared as | Result |
|---|---|
| two migrations behind, variable unset | the container exits 1 within ten seconds, five runs of five — three of the server command against the development tree, two of the built image; no worker boots and the port never opens |
| two migrations behind, variable set | `lineage ok: 2 migration(s) to apply`; both applied; `GET /api/` 200 after 15 s. In the built image held to 0.25 CPU and 512 MiB: 74 s |
| rolled back to before the split (`assets` at 0010) | all eleven applied; 200 after 27 s; the 49 machines already there kept their rows and gained a UUID |
| carrying the other line's 91 migration names | `STOP`, exit 1, `migrate` never started, `django_migrations` at 946 rows before and after |
| one release *ahead* of the image, as after a rollback | starts normally, with a note |

The check on its own was also measured to write nothing: the database's
insert, update and delete counters were identical before and after it with
nothing pending, with migrations pending and allowed, and with them refused.

**Not verified:** no command below has been run against Azure, the image has
not been built by ACR for `linux/amd64`, and how long the start script takes at
`aimms-dev`'s size is an estimate (13.2).

### 13.1 Rights the operator needs

| To do | Needs | Why not something smaller |
|---|---|---|
| Queue the build | `Container Registry Tasks Contributor` on registry `aimms` | `az acr build` is an ACR Task. `AcrPush` is data-plane only and cannot queue one. |
| Read the built digest | `AcrPull` on the registry | — |
| Read and update the apps, follow their logs, `exec` into them | `Container Apps Contributor` on the web and worker apps | — |
| Let an app's identity read Cosmos | Owner, Contributor or `DocumentDB Account Contributor` on `epconchatcosmos9d6b` | `Cosmos DB Operator` lists `sqlRoleAssignments/write` under `notActions`, by design |

### 13.2 Commands

**How migration works here, because it decides the order.** The image does not
migrate at start, and it will not serve with migrations pending:
`InvenTree.apps` logs `INVE-W8: Database Migrations required` and exits.
`gunicorn.conf.py` sets `preload_app = True`, so the application is loaded in
the gunicorn master and that exit is the master's. The container ends, is
restarted, and ends again. There is never a running container to `exec` into
and the revision never becomes ready; on Microsoft's description of single
revision mode the previous revision keeps all the traffic meanwhile — safe,
and stuck.

So the migration is applied by the container's own start script, before the
server is started, when `AIMMS_MIGRATE_ON_START=True` is set on the web app
(`contrib/container/init.sh`):

1. **the lineage check** — refuses if this code has migrations to apply and the
   database already holds migrations it has no file for (13.0). It writes
   nothing. A refusal exits non-zero, the script stops there, and the previous
   revision carries on serving;
2. **`migrate --noinput`** — each migration in its own transaction, so a
   failure leaves the earlier ones applied, the script stopped, and the server
   not started;
3. the server.

`INVENTREE_AUTO_UPDATE` is not used for this. It would also migrate at start,
but it never asks whose database it is, it switches maintenance mode on *in
that database* before migrating, and it starts the server even when a
migration has failed.

The July rollout in section 12 had no migrations, so this path has never been
exercised against Azure.

**Three things about `aimms-dev` the commands allow for.** All three come from
the July export and are re-read in step 0.

- *Memory.* The built image's server, idle, holds 546 MiB when it is given
  room. Held to the export's 512 MiB it starts and answers, at 460 MiB — 90% of
  the limit before a request has arrived. A management command is a second
  copy of the application, 330–350 MiB at peak, and the bootstrap in section 14
  runs nine of them through `exec`. Tried at 512 MiB, one `showmigrations`
  had not finished after five minutes, during which gunicorn logged six workers
  killed for memory. At 2 GiB the same command took 7 seconds.
- *Scale to zero.* With no HTTP request for 300 seconds the app scales to no
  replicas, and an `exec` session does not come in through the app's ingress —
  a long command can lose its container under it.
- *Start-up time.* The export defines no probes. Microsoft's documentation
  gives the default for an app with ingress as a TCP start-up probe on the
  ingress port, once a second for 240 failures, and says a single-mode app's
  previous revision keeps all traffic until the new one is ready. On that
  reading a container has four minutes to open its port. The start script
  boots the application three times before it does (check, migrate, server).
  Held to a quarter of a CPU the built image took 74 seconds to answer, on this
  machine's cores; a quarter of an Azure core will be slower by a factor this
  repository cannot measure, so the margin is not a comfortable one. At 1 CPU
  it took 20. Neither the default probe nor the timing has been observed on
  this app. If the default does *not* apply, nothing restarts the container,
  but traffic moves to the new revision at once and the site is unavailable
  for as long as the start takes.

Step 2 therefore raises the app to 1 vCPU and 2 GiB and holds one replica up.
Step 7 brings it down again — but to 1 GiB, not to the 0.5 GiB of the export,
which this image no longer fits in.

```bash
az account set --subscription 5b75a75a-fff3-4d72-a3e9-5e16cb6a8687
RG=EpconChat
ACR=aimms                                 # the registry's NAME, as 12.4 used
LOGIN=aimms-hjcxb6epgvhgbyge.azurecr.io   # the login server; not a registry name
APP=aimms-dev                             # the app that passed 13.0
REPO=aimms-dev
SHA=11cec10e58c04d4eea065b6bcb7a414a130d4f9c
DATE=2026-10-02T14:28:46+03:00
TAG="inventree-aniket-${SHA:0:12}"

# 0. Look before touching, and keep the output: step 7 and 13.4 need it.
az containerapp show -n "$APP" -g "$RG" -o json --query "{image: properties.template.containers[0].image, revision: properties.latestReadyRevisionName, mode: properties.configuration.activeRevisionsMode, resources: properties.template.containers[0].resources, scale: properties.template.scale, probes: properties.template.containers[0].probes}"
PREVIOUS_REV=$(az containerapp show -n "$APP" -g "$RG" --query properties.latestReadyRevisionName -o tsv)

#    Every app in the group, with the database it points at. The worker for
#    this rollout is the one whose command is `invoke worker` AND whose
#    database is the same as $APP's. A null database means the value is held as
#    a secret; compare those in the portal.
az containerapp list -g "$RG" -o json --query "[].{name: name, image: properties.template.containers[0].image, command: properties.template.containers[0].command, args: properties.template.containers[0].args, db_host: properties.template.containers[0].env[?name=='INVENTREE_DB_HOST'].value | [0], db_name: properties.template.containers[0].env[?name=='INVENTREE_DB_NAME'].value | [0], auto_update: properties.template.containers[0].env[?name=='INVENTREE_AUTO_UPDATE'].value | [0], min_replicas: properties.template.scale.minReplicas}"

#    Then the pre-flight in 13.0. Do not go on until it says "proceed".

# 1. Build in ACR from the exact public commit. Section 12 found that an image
#    built there did not carry the commit ARGs into ENV; the local BuildKit
#    build of this commit did. Step 2 sets them on the revision either way.
az acr build --resource-group "$RG" --registry "$ACR" \
  --image "$REPO:$TAG" \
  --file contrib/container/Dockerfile --target production --platform linux/amd64 \
  --build-arg commit_hash="$SHA" --build-arg commit_date="$DATE" \
  --build-arg commit_tag="$TAG" \
  "https://github.com/EQUA-AI/InvenTree.git#$SHA"

DIGEST=$(az acr repository show --name "$ACR" --image "$REPO:$TAG" --query digest -o tsv)
IMAGE="$LOGIN/$REPO@$DIGEST"

# 2. Web app: the new image, telemetry switched on, migrations applied by the
#    start script. A named revision, so it can be followed without guessing.
SUFFIX="r${SHA:0:8}"
NEW_REV="$APP--$SUFFIX"
az containerapp update -n "$APP" -g "$RG" --image "$IMAGE" \
  --revision-suffix "$SUFFIX" --no-wait \
  --cpu 1.0 --memory 2.0Gi --min-replicas 1 \
  --set-env-vars INVENTREE_COMMIT_HASH="$SHA" INVENTREE_COMMIT_DATE="$DATE" \
                 AIMMS_COSMOS_PUMPHOUSE_ENABLED=True AIMMS_MIGRATE_ON_START=True

#    Follow it. Repeat the command if it says there is no replica yet; if the
#    revision never appears, `az containerapp revision show` (step 3) says why.
#    If step 2 itself has to be repeated, change SUFFIX: a suffix is used once.
az containerapp logs show -n "$APP" -g "$RG" --revision "$NEW_REV" --follow --tail 200
```

What the log says decides what happens next:

| The log shows | It means | Do |
|---|---|---|
| `lineage ok: N migration(s) to apply (…)`, then an `Applying … OK` line for each, then `Migration step finished - starting the server` | migrated, and the server is starting | step 3 |
| `STOP: this code has … to apply, and this database already holds … it has never seen` | another line migrated this database; nothing was changed. The container restarts and prints the same refusal each time | 13.4, and do not work around it |
| a traceback under an `Applying …` line | that migration failed and was undone; the ones before it stay applied; the server was not started and the previous revision is still serving | read the error; 13.4 if it cannot be fixed forward |
| the `Applying … OK` lines, and then the container restarting without ever reaching the server | the start-up window, not the migration: the migrations are in | `az containerapp update -n "$APP" -g "$RG" --remove-env-vars AIMMS_MIGRATE_ON_START` — the start is then one boot instead of three, at the size step 2 set |

```bash
# 3. Confirm the revision took the traffic, and that the schema is where the
#    image expects it. `exec` works now: nothing is pending, so every
#    management command runs.
az containerapp revision show -n "$APP" -g "$RG" --revision "$NEW_REV" -o table \
  --query "{health: properties.healthState, state: properties.runningState, traffic: properties.trafficWeight, replicas: properties.replicas}"
az containerapp exec -n "$APP" -g "$RG" \
  --command "python src/backend/InvenTree/manage.py showmigrations assets"

# 4. Worker - after the migration, never before. It runs the same gate, so on
#    the new image it cannot start until the migration is in; and a worker with
#    INVENTREE_AUTO_UPDATE on would migrate by itself, without the check. Leave
#    AIMMS_MIGRATE_ON_START off here, so that exactly one container migrates.
WORKER=<the worker found in step 0>
WORKER_PREVIOUS_REV=$(az containerapp show -n "$WORKER" -g "$RG" --query properties.latestReadyRevisionName -o tsv)
az containerapp update -n "$WORKER" -g "$RG" --image "$IMAGE" \
  --set-env-vars INVENTREE_COMMIT_HASH="$SHA" INVENTREE_COMMIT_DATE="$DATE" \
                 AIMMS_COSMOS_PUMPHOUSE_ENABLED=True

# 5. Let both identities read the telemetry container, and nothing else. The
#    web app reads history per request; the worker polls. With no worker, drop
#    "$WORKER" from the list.
for A in "$APP" "$WORKER"; do
  az containerapp identity assign -n "$A" -g "$RG" --system-assigned --output none
  PRINCIPAL=$(az containerapp identity show -n "$A" -g "$RG" --query principalId -o tsv)
  az cosmosdb sql role assignment create \
    --account-name epconchatcosmos9d6b --resource-group "$RG" \
    --role-definition-id 00000000-0000-0000-0000-000000000001 \
    --principal-id "$PRINCIPAL" \
    --scope "/dbs/aimms/colls/pumphouse_readings"
done

# 6. The data bootstrap: section 14, start to finish, while the app is still at
#    the size step 2 gave it.

# 7. Settle. Take the variable off - left on, every start pays for two extra
#    application boots - and bring the size down. Not to step 0's 0.5 GiB: the
#    server alone wants 546 MiB. --min-replicas is whatever step 0 printed.
az containerapp update -n "$APP" -g "$RG" \
  --remove-env-vars AIMMS_MIGRATE_ON_START \
  --cpu 0.5 --memory 1.0Gi --min-replicas 0
```

What `aimms-dev` should cost to run is its owner's decision, and going back to
0.25 vCPU and 0.5 GiB is theirs to make — but on the measurements above that
size leaves the server at 90% of its memory while idle, and any `exec` into it
takes workers down. If the settled revision does not become ready, the revision
from step 2 goes on serving while the size is reconsidered.

**If step 0 finds no worker for this database.** Nothing polls, so no tile
fills and no limit is evaluated on a schedule. The recorded data is a closed
window, so the sweep can be run by hand from a shell in the web app once
section 14 has activated the stations — `python
src/backend/InvenTree/manage.py shell`, then
`from assets.tasks import poll_cosmos_pumphouse_sources as poll; poll()`. It
returns the number of stations it visited (3, tried locally); repeat it until
the mimic's tiles are filled. Live polling needs a worker: a second Container
App on the same image, database and secrets, with `invoke worker` as its
command, no ingress and one replica held up. Creating one is a decision for
whoever owns the environment and is not prepared here.

Step 5 is scoped to the one container on purpose: the `aimms` database also
holds `telemetry` and `conversations`, and `…0001` is Data **Reader**. If nobody
with the right to make that assignment is available, the fallback needs no
Cosmos rights at all: `aimms-pumphouse-connector` already holds Data Reader on
that container and its owner can issue a second client secret (BLOCKERS.md,
Ask 2), supplied to both apps as a Container App secret behind
`AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and `AZURE_CLIENT_SECRET`. A managed
identity is the better of the two because there is no secret to rotate; the
existing one expires 2027-09-19.

### 13.3 Verification

- `GET https://<app fqdn>/api/` → 200; `/` redirects to `/web`, 200; sign in.
- Inside the container, `ls contrib/cosmos/review contrib/cosmos/limits` lists
  nine files and one. If either is missing the image predates its `COPY` line
  and section 14 will fail on a missing file.
- `showmigrations assets` shows `[X]` through `0020_state_value_changed_at`.
- A pump page shows **Performance** beside Health.
- Worker replica logs show `poll_cosmos_pumphouse_sources` running from the new
  image, and no `AUTH` error code on the checkpoints once section 14 has run.

### 13.4 Rollback

```bash
az containerapp revision copy -n "$APP" -g "$RG" --from-revision "$PREVIOUS_REV"
```

That makes a new revision from the old one's template — its image, its
environment, its size — and is the same command whichever way the rollout
stopped. The worker goes back the same way, from `$WORKER_PREVIOUS_REV`.

**Before the migration ran** (a `STOP`, a failed build, a start that never
reached `Applying`), nothing in the database has changed and that is the whole
of it.

**After it ran**, the schema stays where the rollout left it, and an older
image is not fully at home there. It starts, it reads, and it updates rows that
exist. What it cannot do is *create* a machine or a signal binding: this line
added seven columns that are `NOT NULL` with no database default, and an image
that has never heard of them leaves them out of its `INSERT`.

| Table | Columns | Added by |
|---|---|---|
| `assets_assetmachine` | `asset_type`, `source_context`, `source_key`, `source_namespace`, `uuid` | `assets.0011` |
| `assets_machinesignalbinding` | `dictionary_hash`; `vote_group` | `assets.0014`; `assets.0019` |

Giving them defaults closes that, changes no row, and is harmless to the new
image, which always supplies its own values. From a shell in the rolled-back
app, `python src/backend/InvenTree/manage.py dbshell`, then:

```sql
alter table assets_assetmachine
  alter column asset_type set default 'equipment',
  alter column source_context set default '{}'::jsonb,
  alter column source_key set default '',
  alter column source_namespace set default '',
  alter column uuid set default gen_random_uuid();
alter table assets_machinesignalbinding
  alter column dictionary_hash set default '',
  alter column vote_group set default '';
```

Both halves were tried on the copy: the two `INSERT`s fail without the
defaults and succeed with them. Rolling forward again needs nothing undone.

The migrations do reverse — `migrate assets 0010` and `migrate part 0154`, run
from the *new* image, undid all eleven cleanly on the copy — but that drops the
equipment registry and every pump-station record with it. It is a way to
abandon the rollout, not to roll it back.

### 13.5 What the 2026-09-26 preparation got wrong

Recorded because all four were written with confidence and none had been run:

1. **The registry name.** It passed `aimms-hjcxb6epgvhgbyge` to `--registry`.
   That is the login server's prefix; `az` refuses it outright (`Registry names
   may contain only alpha numeric characters`). The registry is `aimms`, as 12.4
   used when a build actually ran.
2. **The build right.** It asked for `AcrPush`, which cannot queue an ACR Task.
3. **The worker.** It named `worker-revision.yaml` as the target's worker; that
   manifest belongs to a different database.
4. **The migration.** It said to update the app and then run `migrate` through
   `exec` once the revision was ready. With migrations pending the revision is
   never ready and there is no container to `exec` into (13.2). Reasoning from
   gunicorn's usual behaviour gets this wrong too: without `preload_app` a
   worker that exits at load is respawned and the master stays up, which looks
   like a container one could migrate from. This image preloads, and the only
   way to know was to start the server the way the image does.

And it never said which databases the image is compatible with, which is the
omission that matters most (13.0). Nor that a rollback after the migration
leaves an older image unable to create machines or bindings (13.4), having
called the migrations "additive" as though that settled it.

## 14. Pump-station data bootstrap — local Postgres to Azure Postgres

> **Status:** Verified end to end in a clean-room Django `TestCase` against a
> throwaway database (no Azure contact), and guarded by
> `machine_health.tests.test_estate_bootstrap` /
> `test_bootstrap_replay` so the committed artefacts cannot drift out of
> working order, and by `test_activation_cursor`. **No blockers remain in the
> bootstrap itself**; see 14.5 for what each of the three was, since each failed
> silently.
>
> **It is not a route to the `equa/customizations` environment.** Every command
> here assumes a database on *this* line - `assets` at `0011_equipment_registry`
> and onward - running the image from 13.2. Run the pre-flight in 13.0 first. On
> a database the other line has migrated, the commands either fail on a missing
> column or, worse, succeed against a schema this code does not describe.

The code in sections 12 and 13 deploys an application that can read the plant's
telemetry and draws nothing with it. What turns a reading into a tile — which
source tag is which catalogue parameter, its unit, its display name, its limits
— is **data**, and it lives in a database until it is exported. A deployed
database has users, parts and stock; it has none of this.

| What | Rows locally | Travels with the image? |
|---|---:|---|
| Catalogue parts and parameter templates | 20 / 153 | No — rebuilt by `load_pump_catalogue` |
| Stations and pump bays | 3 / 30 | No — created by `onboard_pumphouse_estate` |
| Equipment components | 451 | No — created by the same command |
| Dictionary points | 2195 | No — created by `onboard_pumphouse_estate` from each station's `snapshot` (14.5) |
| Bindings (approved points) | 1452 | No — created by `apply_dictionary_review` |
| Cached current values | 1452 | No — written by the poller after `--activate`, or by `import_pumphouse_dump` (14.5) |
| `HealthSource` and its `data_ranges` | 1 | No — hand-created, see 14.2 |
| Ingestion checkpoints | 3 | No — created by `--activate` |
| Signal limits (armed bounds) | 404 | No — applied by `apply_signal_limits` (14.3.6) |

Nothing here is telemetry. The readings stay in Cosmos and are read per request.

**There is no dump file to move.** Every input the commands below read is tracked
in the repository and copied into the production image:

| Input | Path | How it reaches production |
|---|---|---|
| Pump catalogue | `src/backend/InvenTree/part/catalogues/pump_systems.json` | inside the backend tree |
| Mimic layout | `src/backend/InvenTree/machine_health/layouts/pumphouse.layout.json` | inside the backend tree |
| Estate manifest, tag shapes, review packs | `contrib/cosmos/review/` (9 files) | `COPY contrib/cosmos/review` |
| Alarm limits | `contrib/cosmos/limits/pumphouse.limits.json` | `COPY contrib/cosmos/limits` |
| The commands themselves | `src/backend/InvenTree/**/management/commands/` | inside the backend tree |

What is **not** in the repository, and has to be supplied to the deployment:

- the `HealthSource` row itself - endpoint, database, container (14.2);
- read access to Cosmos for the web app's and the worker's identity (13.2 step 5);
- the `AIMMS_COSMOS_PUMPHOUSE_ENABLED` setting, on both (13.2 steps 2 and 4);
- a worker that shares the database - the poller runs nowhere else (13.0);
- a **new image build**. The files being in git is not the same as their being in
  the running container: an image built before the two `COPY` lines has neither
  directory, and the commands fail on a missing file.

### 14.1 Prerequisites

The rights in 13.1, a database that passed the pre-flight in 13.0, and the
image from 13.2. That image carries both directories the commands read. One
built before **`55a1823fd`** has neither and every command below fails on a
missing file.

**An image between `55a1823fd` and `9e5f45fb6` is the dangerous one.** It has
`contrib/cosmos/review` and lacks `contrib/cosmos/limits`, so the bootstrap
succeeds and `apply_signal_limits` then fails on a missing file, which
means a deployment can complete every step here, draw every tile correctly and
still be unable to raise a single alarm — because with no limits `classify()`
returns `unknown` for all 1,452 bindings. Check `ls contrib/cosmos/limits`
inside the container before running 14.3.6. Migrations are applied by the
rollout itself (13.2 step 2); step 3 there confirms it, and nothing below runs
until it has — a management command exits with `INVE-W8` while any is pending.

A `Client` is **not** needed: `assets.0009_default_client_backfill` creates an
active `internal` client on every migrated deployment, and `onboard_estate`
only requires that one exist and be active.

### 14.2 Create the source (admin or shell — nothing creates it for you)

`grep` over the backend finds no management command, fixture or API that
creates a `HealthSource`; the only routes are the Django admin
(`assets/admin.py`) and `manage.py shell`. It must carry:

| Field | Value | Why |
|---|---|---|
| `connector_type` | `cosmos_pumphouse` | exact string; `cosmos_pumphouse_replay` is rejected by `onboard_estate` |
| `config.endpoint` | the account URI | connector refuses without it |
| `config.database` / `config.readings_container` | `aimms` / `pumphouse_readings` | same |
| `secret_ref` | **empty** | a key is accepted only against an emulator host; against Azure the connector raises `CosmosConfigError` and authenticates via `DefaultAzureCredential` |
| `client` | the active `internal` client | `onboard_estate` refuses without it |
| `freshness_threshold_seconds` | **300** | see the trap below |

The identity `DefaultAzureCredential` resolves to needs **Cosmos DB Built-in
Data Reader** on the `pumphouse_readings` container - 13.2 step 5 makes that
grant for the web app and the worker. Do not grant it write, and do not widen it
to the database: `aimms` also holds `telemetry` and `conversations`.

**Freshness trap.** The 300-second default applies only when `connector_type`
is passed as a constructor keyword. A source created through the admin takes
the model default of **900**, so stale readings would go unflagged for fifteen
minutes against a five-minute validity window. Set it explicitly.

All four of `onboard_estate`'s refusal conditions — inactive source, wrong
connector type, no client, inactive client — produce the **same** message,
`Select an active Cosmos source with an explicitly assigned active Client`, so
it does not tell you which one is wrong. Check all four.

### 14.3 Sequence

Run these while the app is still at the size 13.2 step 2 gave it. Each one is a
second copy of the application beside the server: at the 0.5 GiB of the July
export a single `showmigrations` had not finished after five minutes and took
six of the server's workers with it (13.2). If `exec` will not take a command
with arguments, open a shell with `--command /bin/bash` and run the same lines
there without the `$EXEC` prefix.

```bash
# APP and RG as set in 13.2 - the app that passed the pre-flight in 13.0.
EXEC="az containerapp exec -n $APP -g $RG --command"
SRC=<health-source-pk>

# 14.3.1  Catalogue the review packs map onto, by IPN and parameter name.
$EXEC "python src/backend/InvenTree/manage.py load_pump_catalogue"

# 14.3.2  Read throughput, BEFORE data_ranges exists (see 14.4).
$EXEC "python src/backend/InvenTree/manage.py benchmark_pumphouse_reads --source $SRC"

# 14.3.3  Record the window. MUST precede --activate: activation reads it to
#         decide where each cursor starts, and a cursor only moves forward.
$EXEC "python src/backend/InvenTree/manage.py discover_data_range --source $SRC \
    --from 2025-07-01 --to 2025-07-13"

# 14.3.4  The whole estate, previewed. One transaction; writes nothing.
$EXEC "python src/backend/InvenTree/manage.py onboard_pumphouse_estate \
    contrib/cosmos/review/estate.manifest.json --source $SRC --dry-run"

# 14.3.5  The same command for real: registers 3 stations and 30 bays, imports
#         each station's dictionary from the tag file the manifest names,
#         applies its review, then binds and opens an ingestion checkpoint at
#         the recorded window's end. The next poll fills the tiles.
$EXEC "python src/backend/InvenTree/manage.py onboard_pumphouse_estate \
    contrib/cosmos/review/estate.manifest.json --source $SRC --activate"

# 14.3.6  Arm the reviewed limits. Until this runs there are no bounds, so
#         classify() returns `unknown` everywhere and nothing can alarm.
#         Preview first: the dry run reports how many alarms the file raises on
#         the estate as it stands and then discards them, which is the number
#         worth seeing before arming anything. Locally: 29 machines, 0
#         breaching. Applying also evaluates, so a breach present at arming
#         time opens its anomaly immediately rather than waiting for a poll.
$EXEC "python src/backend/InvenTree/manage.py apply_signal_limits \
    --limits contrib/cosmos/limits/pumphouse.limits.json --dry-run"
$EXEC "python src/backend/InvenTree/manage.py apply_signal_limits \
    --limits contrib/cosmos/limits/pumphouse.limits.json"

# 14.3.7  Record how long each reading has held its value. Activation enters
#         the window five minutes before its end, so without this the
#         "Unchanged for" figure on the mimic covers only that last slice and a
#         bay frozen for ten days reads as frozen for five minutes. Walks the
#         recorded window read-only; writes one field. Wait for the first poll
#         after 14.3.5 so the cached states exist to be written to.
$EXEC "python src/backend/InvenTree/manage.py backfill_value_changed \
    --source $SRC --dry-run"
$EXEC "python src/backend/InvenTree/manage.py backfill_value_changed --source $SRC"
```

Expected after 14.3.4, asserted by the tests:

| Station | Bays | Approved (= bindings) | Withheld with a reason |
|---|---:|---:|---:|
| Cedar Creek `PH_3` | 14 | 802 | 103 |
| Millbrook `PH_2` | 12 | 452 | 467 |
| Maple Grove `PH_7` | 4 | 198 | 173 |

`estate.manifest.json` is the manifest — **not**
`contrib/pump-cassandra/estate.json`, which carries commentary keys the
onboarder rejects outright (`Manifest requires version 1 and stations only`;
the top level may hold `version` and `stations` and nothing else). Each station
record names a `snapshot` and a `review`, resolved relative to the manifest, so
the dictionary import and the review application happen inside the one command.

The manifest deliberately omits `source_context`. `read_manifest` decodes
numbers as `Decimal` to avoid losing precision, and `AssetMachine.source_context`
is a `JSONField` whose `full_clean()` rejects a `Decimal` with `Value must be
valid JSON` — so a station carrying a coordinate or a lift head cannot be
onboarded while that field is populated. It is descriptive metadata (real plant
name, coordinates, rated power) and none of it is needed to register a station
or draw a reading, but it is a latent bug in the shipped code worth knowing.

`register_station` is idempotent: a second run returns the existing
registration rather than duplicating it, and refuses loudly if a station
already exists under a different client or public UUID. Re-running the whole
command is how an interrupted rollout is resumed; the tests assert it.

### 14.4 Verification

`check_pumphouse_readiness --source $SRC [--probe]` is the readiness audit, and
**it can never report `ready: true` for this estate** — `pumphouse.layout.json`
ships with `review_status: provisional`, and the 743 deliberately withheld
points keep `pending_points` above zero. Read its issues; do not gate on its
exit code. `station_checkpoint_allowlist_mismatch` is expected and benign
between the dry run and 14.3.5 — onboarding adds the station UUIDs to the
allowlist but creates no checkpoints until `--activate` — as is any pending
count.

`benchmark_pumphouse_reads` must run **before** `discover_data_range`: once
`data_ranges` is recorded it always exits non-zero with `documents: 0` and
`window_complete: false`, which is the clamp working, not a connectivity fault.

`deploy_preflight --json` audits migrations and role coverage only. It is
unrelated to this bootstrap and always exits 0, so it cannot gate anything.

### 14.5 Closed blockers

All three are closed. They are kept here because each was a way the bootstrap
failed silently, and the tests that now hold them shut are named.

**Dictionary creation.** The review packs update points and do not create them,
and nothing in the image created them. The manifest now names a `snapshot` per
station: a tag-shape file listing exactly the tags that station reports, with
placeholder values. The dictionary is built from the *shape* of a snapshot, not
from what it measured, and the review sets every data type and unit afterwards
— so no plant telemetry is committed, and the tag names were already in the
review packs. Coverage is exact: 905, 919 and 371 paths, none missing and none
spurious.

**The packs would have been refused.** Each exported pack pinned a
`dictionary_hash` taken *after* review, covering every point's `status`,
`review_note`, `unit`, `component` and `template`. A freshly imported
dictionary is unreviewed, so the hash could never match and
`apply_dictionary_review` raised `Dictionary changed; export a fresh review
pack.` The committed packs carry none, and
`test_bootstrap_replay.test_the_pack_carries_no_post_review_hash` fails if a
re-export puts one back.

**The tiles would have stayed empty for ever.** `activate_station` opened every
checkpoint five minutes before the wall clock. For a station whose history is a
recorded window that is ~442 days *past* `read_ceiling`, and a cursor only
moves forward, so it could never come back: the poller would read zero
documents for ever and report nothing, because finding no documents is not an
error — `last_poll_at` advancing, `last_error_code` empty, `last_success_at`
set, and blank tiles beside working charts. Activation now enters a recorded
window five minutes before *its own* end, which is the same five minutes of
validity measured against the clock the data actually has. The condition
mirrors `read_ceiling` exactly, in milliseconds, so the cursor and the horizon
cannot disagree about which windows are recorded.

This is why 14.3.3 must precede 14.3.5. Activate before the window is
recorded and the cursor is placed at the wall clock, which is the bug this
closed — and it is not repairable afterwards, because the cursor is
forward-only. `test_activation_cursor` asserts the cursor lands below the
ceiling; `test_estate_bootstrap` asserts it for the real estate.

### 14.6 Rollback

`apply_dictionary_review` is one transaction per file: a failure writes
nothing, and `--dry-run` rolls back after reporting counts. Re-running the same
file is safe and duplicates nothing.

There is **no command that undoes an approval.** The only inverse is a
hand-written corrective review file with a `withhold` section, and it is a
partial undo: it returns the point to `draft` and records a reason, but leaves
the component and template assignment in place. The review is applied inside
14.3.5, so treat that step as one-way and dry-run it first (14.3.4).

`apply_signal_limits` is re-runnable and is the only way to change a limit: a
bound edited by hand is erased the next time activation decides a point's
meaning changed. Re-running it on an already-armed estate reports
`applied : 0` and still evaluates, so it is also the way to ask "does anything
breach these limits right now".

`onboard_pumphouse_estate` is idempotent and safe to re-run. There is no
command that deactivates a station or removes a checkpoint.
