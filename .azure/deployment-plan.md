# Azure Deployment Plan

> **Status:** Approved

**Active rollout:** Section 13 below is the October 5 AIMMS test-repair release.
Sections 1–12 are preserved historical `aimms-experimental` records, not current
approval or validation proof for `aimms-dev`.

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

## 13. AIMMS verified test-repair release — 2026-10-05

### 13.1 Scope and source

- User request: commit and push the isolated changes, deploy the Azure Container
  App, then rerun failed or unavailable WP-00 checks and report actual results.
- Branch: `feat/aimms-maf-harness`, exact approved base
  `05d47269bdd58febafea6f91542ad37b54ed7fdc`.
- Protected original checkout: `/home/lokesh/Documents/mbpro/InvenTree`; no WIP
  import, reset, staging, account/RBAC change, PR or AI-generated issue.
- Recipe: **AZCLI, existing-resource image update**. No AZD initialization,
  Bicep/Terraform provisioning, new integration, SDK migration, flag activation,
  demo-data loader or real outbound email/order test.
- Build only the exact published full commit, using the repository Dockerfile's
  explicit `production` target, `linux/amd64`, and actual commit hash/date.
  Resolve its new unique ACR tag to a digest; never deploy a mutable tag or dirty
  worktree. The new plan text is documentation, not additional runtime behavior.
- Durable evidence:
  `/home/lokesh/Documents/mbpro/aimms-maf-migration-evidence/publication-20261005/`.
- User confirmed the existing subscription, East US 2 staging target and
  conditional web/worker alignment, and required preservation of **all current
  database data and every shared consumer**. This approves the scoped plan, not
  an unqualified migration, data reset or feature/integration activation.
- Publication allowlist: the 30 frozen repair/planning paths recorded in
  `release-allowlist.json`, plus this deployment-plan update. Exact final
  commit/index hashes and remote branch read-back are release gates.

### 13.2 Existing Azure context and observed baseline

| Attribute | Observed target |
|---|---|
| Classification | Existing development/staging deployment, not assumed disposable |
| Subscription | Microsoft Azure Sponsorship, `5b75a75a-fff3-4d72-a3e9-5e16cb6a8687` |
| Resource group / region | `EpconChat` / East US 2 |
| Container Apps environment | `epcon-ai-env`, Consumption profile, Succeeded |
| Web app / container | `aimms-dev` / `aimms-dev` |
| Companion worker / container | `aimms-dev-worker` / `aimms-dev-worker`; conditional alignment confirmed by user |
| Registry / repository | ACR `aimms`, `aimms-hjcxb6epgvhgbyge.azurecr.io`, repository `aimms-dev` |
| Serving web revision | `aimms-dev--0000132`, Healthy / Running, 100% traffic |
| Worker revision | `aimms-dev-worker--0000062`, Single revision mode |
| Current immutable image | `aimms-hjcxb6epgvhgbyge.azurecr.io/aimms-dev@sha256:401114b23a56ca50ae8f7814bb76d68e9ff52a9d12e9012fd99b75ac1f5bac94` |
| Public endpoint | `https://aimms-dev.kindpebble-bfe407e4.eastus2.azurecontainerapps.io` |
| Migration-owner posture | Web `INVENTREE_AUTO_UPDATE=True`; worker `False` |
| Worker startup | `invoke worker`; preserve existing queues, secrets, mounts and auto-update false |
| Web image pull | Existing system identity has ACR-scoped `AcrPull`; do not create or change RBAC |
| Worker image pull | Existing registry secret reference; do not read or replace its value |

Read-only exec on the exact serving revision reported Python 3.14.7, Django
5.2.17, OpenAI 3.14.1 and `agent-framework-core` 1.0.0b251120. Its commit value is
`3417cf5a233c026abd2f71da0eea3d97d9ec5e85`, frontend build-info is absent, and
candidate-era `aichat.0053` / `assets.0016` files are absent. The first exec attempt
returned transport exit zero but a remote Python syntax error; it is failed
evidence, not a successful attestation. The corrected no-whitespace command
returned actual JSON metadata without application/provider initialization.

### 13.3 Material deployment gate — not a minor image-only assumption

Comparison of the observed commit value to the approved base finds **418 commits,
2,012 changed paths and 601 migration-file changes** (82 added, 515 deleted, four
modified). This includes upstream squashes and fork application/schema changes;
it is not 601 pending SQL operations. Source hashes and actual applied migration
history must establish the real gap before any new web image starts.

Starting a zero-traffic revision is **not** harmless: current web auto-update may
migrate the shared PostgreSQL database before traffic promotion. Traffic rollback
alone cannot undo schema changes. Do not create a candidate revision until the
upgrade/recovery and old/new-worker compatibility gates below pass and the exact
operation scope is confirmed. No shared database backup, migration, data loader,
maintenance-mode or queue change is authorized by this document alone.

### 13.4 Resource inventory and capacity disposition

| Resource type | New ARM resources | Total after rollout | Limit/capacity evidence | Disposition |
|---|---:|---|---|---|
| `Microsoft.App/managedEnvironments` | 0 | Existing `epcon-ai-env` unchanged | Existing environment Succeeded; quota CLI returned `MissingRegistrationForResourceProvider` for `Microsoft.Quota` | No provider registration or quota increase |
| Container App `aimms-dev` | 0 | Existing app, scale remains min 1 / max 2, 1 CPU / 2 GiB per replica | Current ARM template captured; candidate revision temporarily adds replica consumption | Regional spare capacity not proven; candidate admission remains gated |
| Container App `aimms-dev-worker` | 0 | Existing worker, scale remains min 1 / max 1, 2 CPU / 4 GiB | Current ARM template captured; Single mode affects rollback/replacement | Alignment scope confirmed; compatibility/recovery still gated |
| ACR `aimms` | 0 | Existing Standard registry, one new immutable release artifact | Registry Succeeded, public access Enabled | Build/digest verification required; no mutable `latest` |
| PostgreSQL / Redis / AzureFile / identities | 0 | Unchanged resource estate | Shared consumers/recovery still require qualification | No provisioning, credential extraction or presumed isolation |

No quota/capacity success is inferred from unavailable quota data. Infrastructure
compile/ARM what-if and static Bicep/Terraform role checks are not applicable to an
existing image-only recipe; verify preserved existing resources/RBAC instead of
inventing infrastructure to satisfy a template-oriented helper.

### 13.5 Execution and validation checklist

- [x] Verify isolated branch/base and absent remote harness branch; GitHub/Azure
  authentication is available.
- [x] Freeze and stage the exact 30 existing repair paths; hashes match accepted
  closure bytes; complete configured hooks passed without changing source bytes.
- [x] Rerun 102 tooling tests and five supervisor WP-00 regressions, exits zero.
- [x] Capture read-only target, registry, identity, scale, traffic and runtime
  metadata; disclose source/schema gap and quota-query failure.
- [ ] Finish independent pre-commit runtime and tooling reviews.
- [ ] Commit exact reviewed blobs plus this plan; verify commit paths/hashes,
  push only `feat/aimms-maf-harness`, and read back its exact remote SHA.
- [x] Confirm existing subscription/region, target, worker scope and conditional
  deployment plan with the user. Preserve the original checkout.
- [ ] Build exact published source, verify source labels/backend/frontend parity,
  required imports/package consistency and offline application source hashes.
- [ ] Qualify disposable PostgreSQL fresh/observed-source upgrade histories and
  runtime/schema/queue compatibility. No fake migrations or weakened assertions.
- [ ] Establish usable authenticated operator and least-privilege acceptance;
  approval/effect tests use only named isolated reversible fixtures and mocked
  outbound operations. Never extract credentials or invent actors.
- [ ] Confirm existing shared consumers, safe backup/recovery and one migration
  owner before any shared-schema operation; schema rollback needs a tested plan.
- [ ] Run the Azure validation workflow with actual image/readiness/RBAC/policy
  evidence; keep `Validated` unset while any required gate fails or is missing.
- [ ] Deploy verified immutable digest only after all gates pass. Preserve
  existing feature/model/auth/secret/storage/scaling/queue posture. Verify every
  revision/template/traffic write by reading the exact target back.
- [ ] Verify candidate release contracts before moving traffic; align the worker
  only within confirmed scope. Promote with bounded health/auth/worker checks and
  retained compatible rollback. Stop on authorization or schema incompatibility.
- [ ] Rerun remaining WP-00/previous failure and unavailable checks, recording
  exact source/image/config/command/exits/counts/skips and unresolved gates.

### 13.6 Current validation proof (not `Validated`)

| Check | Actual evidence | Result |
|---|---|---|
| Source freeze | Closure SHA-256 comparison, 3,840 recorded files | No drift before this documentation update |
| Scoped local qualification | Accepted two frozen AI/core runs | Each 3,548 passed / 16 disclosed skips; not deployed acceptance |
| Current local tooling | Publication preflight JSON | 102 tests and five supervisor regressions passed |
| Full configured hooks | `prek run --files` on exact reviewed path allowlist | All applicable hooks passed; unmatched file-type hooks skipped |
| Existing source gap | `template-source-gap.json`, scoped Git comparison | Material upgrade gap; deployment blocked pending qualification |
| Existing runtime | Corrected exact-revision read-only exec | Actual package/hash metadata returned; frontend build-info missing |
| Existing pull role | ACR-scoped assignment query for web principal | `AcrPull` observed; no RBAC write |
| Quota CLI | `az quota list` for Microsoft.App / eastus2 | Missing Microsoft.Quota registration; no registration performed |

### 13.7 Rollback and reporting contract

Retain the current immutable digest and serving revision. Before a schema change,
test the actual backup/restore or compatible forward-schema rollback strategy;
never claim that reverting HTTP traffic restores database state. Do not disable
or delete unrelated revisions, queues, records, storage or images. Report commit,
ACR run/digest, actual web/worker revisions, traffic/readiness and each rerun result
separately. If build/deployment/authentication is blocked, state that explicitly
and complete only the safe independent publication/test work.
