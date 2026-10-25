# Pump08 AI-assisted work intake and exercise verification

## Execution ledger — 25 October 2026

Status: automatic trigger-to-work follow-up implemented and locally verified.
Final current-candidate Fable95.00 / Gemini99.00 independently pass the local UI
gate, without rounding or averaging. All functional/recovery/copy findings are
closed. Scoped audit finds and fixes CTA/hover contrast defects; final8/8
desktop/phone light/dark axe runs have zero violations. All eight raw reports
are persisted, including four incomplete manual-review rules. Earlier20 focused
browser cases and49 units, final App/S6 types and build42.28s pass. Human-owned
live closeout/inference/delivery prerequisites remain; no product sign-off.
Earlier scores do not accept this changed candidate.
This is not full AI/demo or physical acceptance.
Branch `inventTree-aniket`; existing uncommitted test-alarm/closeout work is
preserved. Owner now authorizes its local commit, superseding the earlier
no-commit restriction. No push, PR, deployment or Azure mutation is authorized.

## Local commit and Azure migration audit — 25 October 2026

The current changeset includes one new migration:
`tasks.0061_procedure_client_scope`. It adds a nullable client foreign key to
Procedure, unique client/code ownership and an unmixed client/customer scope
constraint. Existing legacy ownership is not inferred or backfilled. It is
already applied to the local `aimms_azure_copy` PostgreSQL database on
`inventree-dev-db`; a fresh read-only migration-graph audit finds no pending
nodes, and `makemigrations --check --dry-run` reports no changes.

Azure application metadata identifies `aimms-experimental` in `EpconChat`, ready
revision `aimms-experimental--0000119`. The current account cannot perform
`Microsoft.App/managedEnvironments/read` for `epcon-ai-env`, so the attempted
read-only shell inspection is denied before database access. Azure applied state
is therefore unverified. Do not reuse the earlier backend-test-fixes report's
“no new migration” conclusion for this newer feature changeset. Deployment must
include migration0061 if it is not already applied. No Azure migration,
deployment, local-data upload, push or PR is performed by this commit task.

The staged secret scan flags only two public scenario identifiers:
`pump08_motor_winding_2026_10_25` and `mapped_signal_investigation_v1`.
Both are non-secret scenario selection labels, not credentials or signing keys.
Exact path/rule/line fingerprints are recorded in `.gitleaksignore`; no file or
rule is excluded. SMTP credential fields remain blank and ignored runtime
configuration, backups and test receipts are not included in the commit.
The existing scoped verification evidence and human-owned live-demo
prerequisites remain unchanged; no new product or physical acceptance is claimed.

## Contract and precedence

Owner supplied:

- `Pump08_BV_IgniteX_Final_Work_Order_and_Verification_Spec.md` (October25;
  canonical creation and three-step exercise verification contract).
- `Pump_08_Fault_to_Fix_Presentation_Recommendations.md` (October24).
- `Pump_08_Motor_Winding_Fault_Execution_Pack.md` (October24).

Source directory: `/Users/Aniketkumar/Desktop/x-WORK/Repo/equaAI/`.

Implement real AI-populated standardized intake with explicit human review and
native compound creation, not a hardcoded success or an AI-labelled heuristic.
Navigate using the returned work-order ID. Preserve actual source association,
idempotent retries, duplicate review, typed assignment authorization and atomic
packet/card creation. Use actual active Avery Patel; do not invent experience,
trade credentials or historical work. Preserve WO-003826.

Three exercise checks are cooler restored (boolean), maximum winding temperature
(number, inclusive maximum124.9°C; prepared W5=94/W6=96), and stable prepared
15-minute trial (boolean). Require governed published revision, actor/time and
job-owned evidence. Any exercise verdict is separate from physical verification,
technical acceptance, operating permission and normal packet-owned closeout.
Never create safety/release authority or alter historian data to make a demo pass.

## Baseline investigation evidence

- Local machine102 is Pump08, client1. Active directory users Avery27, Daniel29,
  Priya31 exist; IDs are observations, not implementation constants.
- WO-003826 remains a backlog bearing-vibration work order assigned to Avery.
- Test-alarm receipt opens the native manual creation form; the form currently
  has only a free-text assignee. Compound creation already owns native
  work-order/packet/card creation and duplicate review.
- Verify & Return is read-only; native versioned step commands already evaluate
  boolean and bounded numerical tests. Release authority remains separate.
- Local general Azure OpenAI endpoint/key are absent. Provider availability and
  any alternate authorized AI plane need verification; no inference is claimed.
- Both requested specialist agents will review the complete brief/source/docs;
  independent final scores apply to the new rendered implementation, not the
  previous feature's scores.

## Implemented behavior

- The actual test-alarm receipt opens the native work-order form. An explicitly
  enabled, configured provider drafts only four bounded text fields, without
  tools, people, parts, deadlines, approvals or work creation. The request has
  a20-second timeout, no provider retry and a2/minute per-user REST limit. This
  opt-in REST lane is separately documented from the interactive-turn quota.
- Actual model/deployment/run/token provenance is signed to the actor, machine
  and source for900 seconds. Human edits and reviewer/time are retained with the
  native Work event. No provider means unavailable, not a heuristic called AI.
- The explicit prepared Pump08 action uses the final supplied specification,
  corrective/high/critical, blank dates/effort, no parts, and a real typed scoped
  owner resolved by username. Assignment uses the native pre-packet command and
  proposes a handover; it does not accept it or invent qualifications/history.
- Human review precedes compound creation. An exact receipt is required before
  redirecting to its actual returned work-order ID. Uncertain requests retain
  their exact body/key through reload. A v2 compound sidecar hashes all planning,
  source, parts, fault and duplicate fields while preserving legacy native
  create hashes. Replays do not reassign, relink, resnapshot or create again.
- The machine lock serializes concurrent creation. Missing, foreign or owned
  sources roll back the entire aggregate. Duplicate review includes bare open
  work such as WO-003826; a deliberate second job requires a typed reason and
  never steals an anomaly. Source-check failures/owned events disable creation.
  Ended synthetic sources are refused by the native command too. AI context
  retains measurement quality/time; stale or bad primary references are not
  presented as current measured numbers.
- `tasks.0061_procedure_client_scope` adds explicit nullable client ownership,
  unmixed customer/client and scoped-code constraints. Legacy ownership is not
  inferred from applicability or a null customer. Scoped author/review/publish
  and apply commands preserve existing customer behavior and native approval.
- The preparation command authors only a draft. The3 required versioned native
  checks require current independently published content, exact snapshots,
  actual completion events/actor/time and owned prepared evidence bytes/hashes.
  Native numerical boundaries and E06 evidence agreement are separate checks.
- False results,125/129°C, obsolete/drifted revisions, foreign/deleted evidence,
  missing actors/events, reopening and N/A cannot produce an exercise pass.
  Disabling Procedures cannot bypass a marked case's completion requirement.
- The new Verify & Return panel records explicit software-exercise results,
  supports negative/retest/refresh, and never supplies technical acceptance,
  operating permission, observed return, service state or automatic closeout.
  All owning-packet/safety/report approval gates remain independent.

Operator steps and exact limitations are in
[`demo/pump08-motor-winding.md`](../demo/pump08-motor-winding.md).

## Observed verification

Evidence root: `data/devscratch/pump08-ai-intake/` (ignored local artifacts).

| Check | Actual result |
| --- | --- |
| Disposable SQLite,16 focused native suites |176 tests;0 failures/errors;3 PostgreSQL-locking skips |
| Disposable PostgreSQL15, final complete run |176 tests pass, including3 actual concurrent-connection races and flag-bypass prevention |
| Model-purpose policy and flag registry |43+150 pytest cases pass; native test-argv adapter, secret-excluded source, no network; one existing unknown asyncio-mode configuration warning per run |
| Frontend units |1137 pass across123 files |
| Types | App build plus S0/S3/S4/S6/S8 pass |
| Production build | Final pass,18.74 seconds; existing large-chunk warnings |
| New rendered browser journeys |22 desktop/phone cases pass: light/dark intake, provider contract, negative/retest checks; source ownership/permission failure, late AI, uncertain retry and legacy mimic creation |
| Root hooks | Final no-change check passes; formatting-only changes are retained |
| Additional native browser regression |72 desktop/phone cases pass: alarm selection/links, final-update reconciliation and service-report workflow |
| Local migration check | `makemigrations --check --dry-run`: no changes |

Initial browser attempts exposed exact-label and modal-focus timing errors in
the new tests and a missing legacy-mimic harness entry setting. Semantic
textbox/keyboard selection and the native harness entry are included; failed
attempts are not counted as passing acceptance. Rendered form inspection also
prompted full-width typed ownership, wrapping symptom/scope/reason fields and
clearer duplicate feedback. Screenshots are Chromium fixture evidence, not a
real inference, Safari or human/product acceptance.

## Local deployment and protected data

Local backend `aimms-r6-viewer-server` is restarted; frontend5173 and backend8000
respond200. The guarded local database is `aimms_azure_copy` at
`inventree-dev-db`, not Azure. A private141,030,387-byte database backup and
unchanged-config backup are retained locally and must never be attached.

The reviewed migration plan contained only `tasks.0061`; it applied successfully.
Only `aimms_procedures_enabled: true` was added to the local configuration;
all prior config bytes remain identical. The AI drafting flag stays default-off
and the general Azure OpenAI endpoint/authentication remain unconfigured.

Actual authenticated localhost reads return200 for the minimal scoped staff
directory (Avery resolves), source/provider context (already-owned source
blocked, provider unavailable) and enabled procedure catalogue. Desktop/phone
browser captures inspect actual typed ownership and Winding6 selection without
triggering a source or submitting work. The temporary read-only session is
deleted; domain writes are0. Two automatic observability-end POSTs were blocked,
not sent. A corrected capture-driver close-button selector is not a product
failure or a claim of live AI inference.

Ten before/after data fingerprints match:1,643 signal states,1,643 bindings,
13 anomalies,1,563 Work records,2,370 events,82 commands,22 packets,1,209 history
records, the two legacy procedure identities and existing bearing WO-003826.
No new live alarm, work order, procedure, prepared result, physical release,
personnel history, SAP request or email has been created in this task.

Observed actual personnel are Avery27 (`cc.avery.patel`), Daniel29 and Priya31
(`cc.priya.shah`), all active and with the checked native procedure/execution
and approval-review permissions. Names in supplied narrative are not authority
to substitute or impersonate a different person.

## Specialist review and remaining boundaries

Both requested Hyperagent specialists separately verified all4 initial uploads:
the29-file source manifest and three complete supplied documents. Fable used
temporary internal specialists. Adopted findings include complete idempotency,
exclusive source ownership, native typed assignment, fail-closed staff scope,
honest model availability and independently governed exercise checks. Explicit
client ownership was selected instead of inferring scope from an unowned legacy
procedure. A v2 sidecar preserves older native retry compatibility.

Gemini's current-scope final score is97.85/100, independently passing the UI gate
after verifying all25 task attachments and the timestamp correction. No blocking
finding; nonblocking polish is the long combobox suffix and step3's checkbox
reset for the next retest. Fable's final score is82.8/100 and fails the gate.
Previous
feature scores do not apply. Each must independently score honestly across
quality, intuitiveness, effectiveness, visual craft, accessibility, safety and
verification; no rounding/averaging or coerced95+ result.

The final independent review is submitted in the existing specialist threads:
Fable `cmvmpn3ez0atu07ad9vgh1op6`, Gemini `cmvmpplx00azo07ad620vim8t`.
Each receives20 separately uploaded/HTTP200-verified final attachments: a complete
63-file source manifest/bundle, classified verification receipts,14 rendered
fixture views and four settled actual localhost desktop/phone views. With the
four initial uploads each thread has24 cumulative files, in batches4 then20;
all three original scenario documents remain in context. The source bundle
SHA256 is `0b153a919f06e563dc4fbf5532d4f044f82ba8389d463eb1d77a8f0c3613591a`.
This ledger-only progress update does not change the reviewed runtime sources.

One material two-file backend correction followed the frozen snapshot: native
signal timestamps are Python datetimes, so provider context now uses
`DjangoJSONEncoder` with `allow_nan=False`. The real-context binding regression
asserts primary and reference timestamps serialize as strings. Both reviewers
received the complete two-file replacement delta with SHA256
`3739d7ef6747386a0c263184225403983437ac576d8b7f5140fb2fd7f53c0607`.
The final task set is25 attachments in batches4+20+1, all separately uploaded200;
the previous feature's61 attachments remain in each thread (86 cumulative).
Final timestamp-inclusive SQLite176 (3 locking skips) and PostgreSQL176 pass;
targeted hooks pass and the local backend is restarted. No further UI or runtime
change follows the delta. All ten protected-data fingerprints remain unchanged.

The owner asks to accelerate. Repeated full-suite work stops; Fable is asked to
finish only its essential blocker/score review. New work follows only a concrete
blocker or the missing provider/publication prerequisites, not optional polish.

### Final review reconciliation — status update

Fable's final verdict verifies25 task attachments and63 source identities plus
the two timestamp replacements, accepts the classified final PostgreSQL176 and
SQLite176 results, and reports no backend safety/authority blocker. It does not
pass the frontend gate. Do not average away the disagreement with Gemini97.85
or reuse the previous feature's95+ scores.

Fable's blocking findings are F1, a pixel-measured2.16:1 light-theme TEST badge
contrast, and F2, an inferred, not yet reproduced cancel/reopen cached-readiness
race leaving Create disabled. Its requested re-score scope also includes alert
contrast, clearer96.0/evidence-agreement guidance and displayed check reasons,
specific draft failure/retry states, own-record refresh behavior, duplicate-link
draft preservation and focused negative/keyboard tests. The broader duplicate
rule is an owner-requested policy change; treat that finding separately from a
proven regression. Optional polish is not a reason to repeat full suites.

Next work is targeted reproduction/remediation of these findings and an honest
Fable re-score, not a claim that the quality gate is complete. No code or data
changes are made during this status-only update. Frontend5173/backend8000 both
respond200. Provider configuration, independent procedure publication and a fresh
live end-to-end rehearsal remain unclaimed prerequisites.

Live inference still requires authorized provider configuration. Actual native
procedure publication requires independent review of the returned draft;
physical execution and permitted packet closeout remain operator-owned. A fresh
live end-to-end alarm/closeout is not claimed while those prerequisites are
missing. Fixtures/native isolated tests are not deployed or physical acceptance.
## Automatic-trigger follow-up — 25 October 2026

Implemented: one confirmed trigger atomically creates the labelled synthetic
alarm and native planned work package, using a pre-reviewed prepared scope and
eligible typed owner, then navigate to the returned work-order ID. Duplicate
review is explicit; a refused work creation must roll back the alarm too.
This is prepared-document automation, not live AI inference or physical service
release. The preview is signed for actor/machine/component/channel and expires
after15 minutes for new work. Committed retries return the frozen receipt even
after expiration or source resolution. One native machine lock serializes the
entire aggregate. Alarm-only old API requests remain backward-compatible.

The exact Pump08 winding-six target uses the canonical October25 scope; other
mapped channels use a cautious investigation plan, never the Pump08 scenario.
Avery is suggested by actual eligible username, not a hardcoded ID. Assignment
eligibility is rechecked at commit. Existing repairs show title/status/packet
state and open in a new tab. Separate creation requires a reason, explicit
review and native assignment authority. WO-003826 is never modified.

Final focused checks pass: SQLite84 (two PostgreSQL-only skips), PostgreSQL15
including five real threaded races,24 targeted units, App/S6 types and production
build19.16s. The governed Pump08 fixture now uses the automatic command before
independent publication and all three native checks. A52-case browser run passes48;
14 focused reconciliations pass all four failed cases and updated automatic
desktop/phone light/dark captures. This is not a single52/52 invocation. Findings
corrected include accessible switch naming, retained alarm-only mode across reload,
timeout classification ordering and destination mocks. One phone interruption
during a source/formatter update is repeated successfully after source settles.

Actual localhost5173: preflight resolves the supplied scope and actual Avery.
One confirmed trigger creates alarm15, Work3856 (`WO-003856`) and packet33, with
one primary card, source ownership, typed Avery27 and the exact canonical scope.
The browser opens the returned work ID. The audit records CREATED, ASSIGNED,
WORK_PACKAGE_CREATED and TEST_EXERCISE_ENDED. The administrator end command
resolves only this synthetic event; the Work remains draft, not completed.
All10 baseline fingerprints match after excluding only this new aggregate;
WO-003826, prior alarms/jobs, signals, limits, history and procedures are unchanged.
Local trigger and ended-test emails are captured in Mailpit and both in-app
categories exist. The ended-test email required processing its one exact queued
mail task; the unrelated queue is untouched. This is local SMTP capture, not
external delivery or proof that a background delivery worker is running.
The one-off task runner's outer transaction closed its logging connection after
SMTP succeeded; actual captured subject/body are verified before acknowledging
that delivered queue item. No resend or fabricated worker-success record.
The full live closeout, human publication and physical release remain
unclaimed. A script's exact44 comparison stopped active-chart capture because
the real unchanged reading is44.000003814697266, not44 exactly. Cleanup safely
ends the sole event; read-only reconciliation proves unchanged measured data.
No second live trigger is made just to replace that precision assertion.

Fable remediation includes source-ready cancel/reopen handling, high-contrast TEST
and verification text, visible prepared-evidence mismatch reasons, exact96°C
versus native≤124.9°C explanation, no own-write conflict flash, differentiated
429/provider/timeout errors, and no repeated automatic inference on reopen.
Re-scores and a human-owned full closeout rehearsal are still pending. No Azure
or Git publication. Mandatory checks are retained; only optional polish and
redundant broad/full-suite runs are skipped at the owner's request.

## Three-blocker review remediation — 25 October 2026

- B1: all five actual native per-check reason codes map to translated, actionable
  operator text, including an explicit safe fallback. The mock now emits native
  codes, not invented prose. A pure unit contract compares the vocabulary with
  the actual Python emitter; browser tests assert the mapped evidence-mismatch
  text and absence of the raw code. A failed bound is not misdescribed as a
  missing completed result: the message correctly says no current passing result.
- B2: preview errors display the server's bounded human detail, the complete
  four-field scope includes symptom and server-provided type/priority/criticality
  and parts count, a native output/live region explains unmet conditions and
  describes the disabled primary action, and combined-command refusal is distinct
  from an unconfirmed alarm-and-work outcome. A definite refusal requires fresh
  preview and renewed human confirmations; uncertain retry retains its exact key.
- B4: a current result newer than the acknowledged pending version clears the
  pending lock but preserves the explicit concurrent-change review. Loading that
  current record clears the pending marker; the race test uses expected versions
  1 then3 and confirms the operator can retest without remounting or overwriting
  an unseen concurrent record.

Fresh focused verification:22 desktop/phone browser cases pass;23 focused units
include seven native-reason contract cases; six native-code/reason cases repeat
after the final passing-result wording. App/S6 types and build21.17s pass.
Backend84 SQLite and15 PostgreSQL evidence is retained unchanged because these
fixes do not alter backend code. No repeated broad/full-suite run and no second
real alarm. Both independent post-remediation scores remain pending.

## Final review residue — 25 October 2026 (in progress)

The independent post-remediation reviews are Gemini98.85 and Fable90.3, not a
passing dual score. Both clear B1/B2/B4. Fable verifies73 source identities and
17 of18 attachments; its phone definite-refusal image returns404 and is not
counted as viewed. Gemini reports18 of18. This differing attachment access is
not silently reconciled as a universal pass; fresh per-reviewer uploads follow.

Scoped corrections: the common limit formatter treats null and undefined as
absent while preserving zero; fixtures carry all six real limit keys. All three
native result-level governance reasons now have actionable translated wording
and a safe unknown fallback, rather than de-underscored machine codes. The
unavailable preview explains the explicit alarm-only alternative without
bypassing test permissions. Existing-work links announce a new tab; safety
helper descriptions and governance copy use native readable body text.
No backend changes, schema migration, new real alarm or physical closeout.
Fresh targeted verification passes20 desktop/phone browser cases and49 units
(37 common monitoring cases including three missing/zero-bound regressions;
12 native reason-vocabulary contracts). App/S6 types and build25.33s pass;
configured hooks pass. All three result-level reasons and unknown fallback are
exercised as presentation-oracle inputs, not a claim that the native backend
emits that combined state. Governance and helper contrast assertions exceed4.5:1
in both themes and viewports. This is not an axe/WCAG certification.
Corrected phone refusal/limit-region captures and both current-candidate
independent re-scores follow. Earlier84 SQLite/15 PostgreSQL evidence is retained
unchanged; there are no backend changes or broad reruns.

Final capture correction: the first modal screenshot showed only its scrolled
upper region, not the actual phone refusal. New region-targeted captures verify
the native definite refusal and preview403; separate limit-region captures show
the correct upper-only bounds. Twelve final contrast/capture repeats pass after
making the combined-refusal title readable in the native light alert; the eight
intermediate repeats are archived, not counted as new coverage. Shared browser
contrast assertions cover actual helpers, refusal titles and governance text.
Final App/S6 types, build55.38s and scoped hooks pass. Both existing reviewer
threads receive all76 complete source identities and28 immutable attachments in
sequential20+8 batches; no scope is omitted and old inaccessible IDs are not
reused. Both fresh independent scores remain required.

## Scoped accessibility closeout — 25 October 2026 (in progress)

The residue scores are Fable94.6 and Gemini99.00; the dual95 gate still fails
without rounding. Both verify28 attachments and76 source identities and close
all B1/B2/B4/R1/R2/R3 defects. Fable requests an explicit accessible/icon new-tab
cue and scoped axe evidence as one bounded path to re-score, rather than a large
optional component refactor.

Independent axe4.10.3 inspection finds two real contrast defects: the white CTA
label on orange9 is4.29:1 and the hovered retest label is4.12:1 on phone light.
The initial audit passes3/8; this is not a passing audit. The CTA label now uses
native black over the filled orange, including its lighter hover; the light
retest label uses native body text in both themes. Existing-work links include
an accessible new-tab name and hidden-from-AT external-link icon in addition to
the visible announcement. No compound-request/receipt/safety behavior changes.

An opt-in test helper injects the pinned axe source from AIMMS_AXE_SOURCE and
checks the selected modal or verification panel against WCAG2 A/AA plus2.1/2.2 AA
tags without disabling any rules. No new project dependency or baseline axe-run
claim: local verification supplies that explicit source. Raw violations and
incomplete checks are preserved; zero violations will not mean certification.
A first final-run attempt encounters an occupied harness port before any test;
it is rerun on a free port without killing the unrelated listener. Final audit,
type/build receipts and both current delta re-scores remain pending.

Final evidence:8/8 explicit scoped axe runs pass on the reviewed trigger modal
and three-check verification panel across desktop/390px phone and light/dark.
No rule is disabled, and all eight full raw reports are written to disk and
bundled with original-byte hashes. A first green replay attached JSON in memory
only; its eight cases repeat with durable report paths rather than inventing
missing raw data. This is repeated evidence, not16 unique journeys. Incomplete
checks remain in the raw reports; zero violations is not WCAG certification.
App/S6 types, build42.28s and configured hooks pass. Production source is stable;
no backend or additional live-alarm mutation. Both reviewers receive a complete
77-source current snapshot plus all16 intentional rendered captures and a single
lossless bundle of all eight raw audits:19 files within one attachment batch.

## Final independent UI gate — 25 October 2026

Both current reviews independently verify19 attachments,77 complete source
identities and8 original raw axe report byte hashes. Fable95.00 and Gemini99.00
pass without rounding or averaging. No P0/P1/P2 remains in this UI candidate.

| Criterion | Weight | Fable | Gemini |
| --- | --- | --- | --- |
| Correctness and safety | 25% | 95 | 99 |
| Trust and provenance | 20% | 96 | 99 |
| Intuitiveness and task value | 15% | 95 | 99 |
| Hierarchy and visual craft | 15% | 94 | 99 |
| Accessibility and responsiveness | 15% | 96 | 99 |
| Architecture and maintainability | 10% | 93 | 99 |
| Weighted total | 100% | 95.00 | 99.00 |

Reviewed production snapshot SHA256:
`712532aded4dffdee5d73feace5b75aa86e95bc1d7e785fe9856e2aaae8cc7b3`.
Current raw-audit bundle SHA256:
`219f18e21261245e525043e7a40b03fc7281f2a191a6504fe00f297ac64b7020`.
Receipt SHA256 is the local manifest's
`68a29bfd936145c0a128704b25bc6d7d0cabf6757901b4119f90e0c2b03205d9`;
Gemini's first prose transcription is malformed, not a changed artifact.
After review only ledger metadata changes; production source remains unchanged.

Critical reconciliation and deferred optional work:

- Four verification-panel audits each retain one color-contrast manual-review
  rule on three native autosize textareas. No exclusion or silent waiver;
  reviewers find normal readable native text, but this is not an independent
  assistive-technology/WCAG certification. A manual contrast check remains P3.
- Current screenshots accessibility-04 and accessibility-10 are misframed limit
  crops showing the underlying table. They do not count as correct limit-region
  evidence. Correct current siblings06/08 and retained residue13/23, plus
  unchanged bounds assertions, substantiate the unchanged limit code.
- The opt-in axe helper returns without auditing when AIMMS_AXE_SOURCE is absent;
  do not call baseline CI an axe pass. Explicit annotation/CI gating is deferred.
- Component/matcher decomposition, raw backend degC and prepared-note spacing,
  long closed-select tag suffixes and toast overlap are nonblocking backlog.
  No optional architecture/polish work is performed after passing this gate.

Available locally on5173, ready for human review of automatic work creation.
Actual15/WO-003856 stays ended/draft, not physically repaired or fully closed.
Live AI inference remains unconfigured. Reliable background mail delivery,
independent human publication/physical authority and a full live multi-actor
closeout rehearsal remain prerequisites for full demo acceptance. Existing
dataset preservation is the recorded reconciliation proof, not a new live
chart-to-closeout claim. No Azure/SAP, new migration, Git publication or parent
product/physical acceptance change.

## Alarm-email sender follow-up — 25 October 2026

Owner supplies the Azure-managed no-reply sender and native configuration
reference. The prepared [SMTP profile](../demo/azure-alarm-email.env.example) and
[activation runbook](../demo/azure-alarm-email.md) use INVENTREE_EMAIL_SENDER with
smtp.azurecomm.net:587 and STARTTLS, not hardcoded alarm transport or recipients.
Both focused native critical/test-trigger/verified-recovery sender tests pass
(2 tests in5.133s); profile contract and configured hooks pass. Anonymous Azure
SMTP negotiation verifies the certificate and TLS1.3, with no authentication or
message. Current viewer remains on isolated Mailpit with absent SMTP credentials.
No real sending, worker/queue change, new live alarm, Azure mutation or migration.
Actual ACS SMTP username/client-secret and an approved verification recipient are
requested securely; external alarm/closeout email delivery remains unverified.
The completed frontend review gate is not external mail acceptance.
