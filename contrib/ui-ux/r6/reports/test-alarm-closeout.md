# Test alarm and service-report closeout — 24 October 2026

Status: implemented on `inventTree-aniket` from `374ac6d24`; isolated verification
and owner-authorized localhost rollout recorded below. Local availability is
verified, not full operator-journey or product acceptance. No Azure, live SAP,
Git commit, push or PR changes.

Follow-up: [Performance labels and synthetic chart overlays](performance-test-alarm-fixes.md)
fixes missing source-catalogue labels and propagates active tests to exact-binding
chart/KPI annotations without modifying telemetry. The owner's newer test9 is
left active; this is separate from the older test reset recorded below.

## Owner-requested local test reset — 24 October 2026, 16:27 UTC

The owner requested ending existing test alarms before creating the next test
personally. UI test7 had already been ended at16:21 UTC through the audited
`end_test` action, reason `it was test alarm`; Work3850 is still draft/version2.
No duplicate end command, completion or service-return claim was issued.

Further inspection corrected the earlier classification of threshold alarms5/6
as genuine: both contain legacy `metrics.test_alarm` dictionaries recording the
original readings. Alarm4 on Pump07 has the same development-test provenance.
On the local `inventree-dev-db` only, native `test_alarm --clear --yes` for
machines101/102 resolved these three conditions through ingestion and threshold
recovery, then restored nine saved state rows exactly. Before writing, every
binding's machine and original observation timestamp were checked; no newer
measurement was replaced. Anomaly metrics retain owner-requested cleanup reasons.
All other state rows are SHA-256-identical before/after; Work3850 is unchanged.

Pump08 winding5/6 now read43.6/44.0°C with normal configured-limit verdicts, and
the Cedar Creek station mimic projection has zero breaches. Idle equipment,
signals without configured limits and missing readings retain their truthful
grey/unknown presentation; no blanket green styling, invented data or threshold
changes. Unrelated machine7's seeded gate alarm remains untouched. Historical
source data, Azure and SAP were not modified. Cleanup receipt:
`data/devscratch/test-alarm-closeout/closeout-live/legacy-test-cleanup.log`.
Authenticated read-only Mac Chromium checks on5173 confirm `No active alarms`,
normal motor readings and the loaded Performance charts (highest winding44°C).
Schematic, motor and full Performance captures were visually inspected. Earlier
partial checks encountered a transient notification and an overly specific chart
text locator; the corrected replay passes and removes its temporary session.
Earlier read-only verification statements below describe those earlier checks,
not this subsequently authorized reset.

## Implemented screens

Equipment action: station → pump → mapped component → configured test signal.
Work Closeout: preview service report → submit → independent approval → native
completion → supervisor verification → test recovery → equipment view.
Apply `masterdesignprompt.md` and `designprompt1.md` with native Mantine,
React, Work permissions, tenant scope and physical return-to-service authority.

## Execution ledger / backlog

| Slice | Contracts | Status / evidence |
| --- | --- | --- |
| Explicit test alarm and cascade | D01, D02, D03, D18 | Implemented; scoped station/pump/component selection, explicit administrator confirmation and request replay; synthetic alarm only, never overwrite plant measurements |
| Retained service report and approval | D11, D12 | Implemented; draft/submitted preview, native command/event ledger, separate reviewer, exact account/evidence/version checks before canonical completion |
| Verify, test recovery, notification and navigation | D09, D10, D18 | Implemented; native supervisor verification, test-only recovery, durable notification intent and native equipment/motor return; real alarms and physical authority remain independent |
| SAP writeback | D05, D12 | Adapter contract and durable dispatch implemented; no real connector supplied. UI says Not configured; no live SAP acceptance or demo acknowledgement |
| Schematic and reference motor | D02, D03, D20 | Synthetic alarm overlay shares the native alarm attention path without changing measured values; labelled test notice, acknowledgement, reduced motion and independent recovery |
| Verification | D20, D21 | SQLite 190 and PostgreSQL 174 pass; browser inventory and frontend checks below. Local fixtures are not authenticated deployment acceptance |

No new migration is added: additive records reuse the native anomaly,
Work command/event and durable closeout-effect ledgers. Real readings, missing
data and unrelated alarms must retain their actual state after a test closes.

## Operator flow

1. An administrator opens **Trigger test alarm** from native Machine Details.
   Choose a scoped pump station, pump, registered component (including a mapped
   electric motor), and a signal with an existing usable critical limit. Confirm
   the exercise. No Cedar Creek identifiers, thresholds or fitted components are
   invented or hardcoded.
2. The server records an explicitly synthetic critical condition and requests
   normal in-app/email delivery to the native authorized alarm audience. A retry
   reuses its exact request key; another key cannot duplicate a standing test on
   that signal. Real telemetry rows are untouched. Corrupt cross-machine mapping
   is not an eligible target.
3. **Create work for this test** opens the existing anomaly-origin Work modal.
   Native planning, assignment, safety, materials, execution and readiness still
   apply. Nothing jumps a new card directly to completion.
4. In Closeout, open **Service report and return**. Preview the current draft,
   submit a retained account/evidence snapshot, and have another authorized
   reviewer approve that exact submission. Resubmission retains prior reports;
   changed account, sources or Work version cannot reuse an old approval.
5. Use the existing **Update maintenance record** command. Packet ownership,
   readiness, capture review, immutable completion reports, maintenance history
   and the native finalization transaction remain authoritative. Linked UI-test
   work always requires report approval; other Work is optionally governed by
   the deployment-wide approval flag.
6. With a real SAP adapter configured, **Write back to SAP** queues a durable
   effect for the frozen completion report. Queued is not acknowledged. An
   ambiguous provider outcome requires reconciliation, not blind replay.
7. **Verify and view equipment** performs native supervisor verification,
   including the configured separation rule, then resolves only this Work's
   synthetic test anomalies. It records a durable recovery notification intent
   and returns to Equipment view. A uniquely mapped pump component focuses the
   existing reference-model motor/bearing/pump/discharge position. A real alarm,
   stale reading or missing value is never forced green.

The recovery email links both the verified Work/report and equipment. It says
the synthetic exercise returned to normal and explicitly says actual telemetry
and unrelated alarms are unchanged. Email transport, recipients and user
preferences must be configured; a recorded dispatch is not external delivery
confirmation. Tests use the local mail outbox, never real SMTP.

## Native contracts and boundaries

- Test API: `GET/POST /api/machine-health/machines/{id}/health/test-alarm/`.
  Feature flag, authenticated administrator, OAuth Work scopes, exact machine
  client scope, selection ownership and explicit confirmation all apply.
- Report API: `GET/POST /api/tasks/work-orders/{id}/closeout/service-report/`.
  Reuses the native Work parent, separate capture/review/verification roles,
  optimistic versions, transactional command ledger and current-authority checks
  before replay. Reads do not create reports; responses are private/no-store.
- Submit/approval records use append-only native Work events and commands.
  Canonical completion additionally validates the exact approved account,
  capture and source digest. The existing frozen completion report is not
  replaced by a client-generated draft.
- Recovery changes only `test_alarm_ui` anomalies with explicit synthetic
  metadata. It never writes signal states, restarts polling, changes SCADA,
  issues operating permission or clears production alarms. New post-report
  alarm links refuse this return command.
- The station mimic reads real and test conditions in one existing batched
  query, preserving its native query budget. Test alarm rows carry synthetic
  quality and labels; the measured point remains unchanged.
- Browser requests retain actor/host/resource-bound keys across reloads, block
  concurrent duplicate clicks, reject mismatched receipts, and preserve unknown
  outcomes. Failed report refresh removes retained approval/action authority.

## Deployment configuration

Existing prerequisites: native Work and Closeout must be enabled, and scoped
users must hold their ordinary action roles. The owner authorized the localhost
configuration below after implementation; no Azure setting is changed.

| Setting | Purpose |
| --- | --- |
| `AIMMS_TEST_ALARMS_ENABLED=true` | Opt into administrator-only synthetic test exercises; default off |
| `AIMMS_WORK_ORDERS_ENABLED=true` | Existing native Work prerequisite |
| `AIMMS_CLOSEOUT_WIZARD_ENABLED=true` | Existing Closeout/report prerequisite |
| `AIMMS_CLOSEOUT_VERIFY_POLICY=supervisor` | Enable native supervisor verification; default remains off |
| `AIMMS_CLOSEOUT_EFFECTS_ENABLED=true` | Dispatch persisted effects after commit; default remains off |
| `AIMMS_SERVICE_REPORT_APPROVAL_REQUIRED=true` | Optional approval requirement for all Work; UI-test-linked Work requires it regardless |
| `AIMMS_SAP_WRITEBACK_ADAPTER=trusted.module.callable` | Only after a real trusted server adapter is provided; default unconfigured |

SAP adapter signature: `adapter(report=<frozen canonical report>,
idempotency_key=<durable effect key>)`. It must return
`{'status': 'acknowledged', 'reference': '<real provider reference>'}`.
Only a definitive no-effect refusal should raise the native retryable-effect
exception. Timeouts, missing acknowledgements and ambiguous outcomes enter
provider reconciliation. No endpoint, credentials or receipt may come from the
browser, and no fake/demo acknowledgement is supplied.

### Owner-authorized localhost rollout

- Primary development URL: `http://localhost:5173/web/`. Pump08 schematic:
  `http://localhost:5173/web/machines/machine/102/equipment/?view=schematic`.
  Built SPA also remains available at `http://localhost:8000/web/`.
- `aimms-r6-viewer-server` now mounts the current `inventTree-aniket` working
  tree read-only, including its newly compiled native frontend. The prior viewer
  data/virtualenv mount is retained read-write. This is an uncommitted local
  build, not an Azure image release. `npm run build` passes with the existing
  large-chunk advisory; build-info records HEAD `374ac6d24` and dirty=true.
- Existing PostgreSQL remains on `inventree-dev-db`, database `aimms_azure_copy`:
  a local copy, not Azure. Before rollout, a private 134 MB custom-format dump
  was taken (`localhost-before.dump`, mode0600, ignored artifacts). No restore,
  model migration, plant-data seed, account/password reset or telemetry write.
  Staging and final `runmigrations --check`: **lineage ok: nothing to apply**.
- Locally enabled: test alarms, Work, Closeout wizard, supervisor verification
  and closeout effects. All-Work approval remains off; UI-test-linked Work still
  requires independent report approval. SAP adapter remains empty/unconfigured.
- Mail uses the dedicated `aimms-local-mailpit` container on the local Docker
  network. Inbox: `http://localhost:8029/`; SMTP has no host-published port and
  no external relay. A clearly labelled configuration email was accepted and
  appears in this inbox. Alarm emails also go here, not to real recipient inboxes.
  Existing notification audiences/preferences are not overridden.
- The dev server uses its native `--noreload --sync` mode for newly requested
  background email tasks, with post-commit durable closeout dispatch enabled.
  No general scheduler/worker is started and no old shared queue is drained.
- Authenticated **Mac Chromium** smoke check: target/report APIs200; actual
  Cedar Creek → Pump08 → Electric Motor → Motor Core RTD1 selection; trigger
  disabled before confirmation and enabled after confirmation; native report
  card and draft preview open. Both live screen regions are captured and
  visually inspected. The temporary administrator session is deleted. No alarm
  is raised, report submitted, approval issued or Work completed by these checks.
  Existing readiness, role and different-reviewer gates still apply.
- No browser page errors. Unrelated AI thread/focus and voice-capability routes
  return404 in this local configuration; no AI availability claim or unrelated
  route repair is included. The test-alarm and report requests succeed.
- Evidence: `localhost/browser-smoke.json`, `localhost/browser-smoke.log`, the
  two live PNGs, `logs/test-alarm-localhost-build.log` and
  `localhost/runtime-check.json`, under the ignored evidence root below.
- Only the temporary staging container is removed. The prior server is retained
  stopped as `aimms-r6-viewer-server-before-test-alarm` with restart disabled;
  new server and Mailpit have restart=unless-stopped and loopback-only ports.
  Rollback: stop the new server, rename it aside, rename the prior server back
  to `aimms-r6-viewer-server`, and start it. No database rollback is needed.

### Port5173 / missing warning follow-up

The owner reported missing blinking and no trigger on port5173. The original
rollout updated the built SPA/backend on8000 but left the already-running Vite
process on5173 in `InvenTree-r6/viewer`, which lacked the new trigger and motor
attention source. This was a frontend deployment mismatch, not a new schema issue.

Only that obsolete Vite process is stopped. A persistent development session now
runs `npm run dev -- --host 127.0.0.1 --port 5173 --strictPort` from this working
tree's `src/frontend`; native API proxy targets the existing localhost8000 backend.
No other checkout is edited. Hard refresh port5173 to discard old loaded modules.

Authenticated Mac Chromium on5173 verifies target, service-report and station-mimic
APIs200; the visible administrator-only header trigger; real station/pump/motor
selection; disabled-before/ready-after confirmation; service-report preview; and
the native Process schematic motor's critical/acknowledged attention. Three final
region captures are inspected, including only the motor marker rather than actual
measured telemetry. No page errors; unrelated AI routes retain the known404s.
Evidence: `localhost5173/browser-smoke.json` and its three PNGs.

The local Pump08 has two acknowledged critical threshold conditions, zero open
UI-test alarms, and motion is not paused. Acknowledged alarms correctly keep
steady red emphasis rather than blinking. A new unacknowledged test condition
uses the blinking path; reduced-motion preferences and the pause control still
stop animation without removing the warning. No real alarm is reopened or
unacknowledged for demonstration. The trigger remains administrator-only: an
operator login does not acquire injection authority from this deployment fix.

Fresh Mac Chromium regressions pass **8/8**: native and synthetic motor alarms,
desktop/phone and light/dark, including blink phases, steady acknowledgement,
reduced motion and recovery. These use isolated fixtures on15302, not owner
alarm mutations; the temporary fixture server stops afterward. Log:
`logs/test-alarm-5173-motor-regression.log`. Changed-file hooks and whitespace
checks pass. The live dev server on5173 remains running.

## Verification and evidence

### Closeout usability, test cleanup and PDF follow-up

The owner could not use Closeout or clear the exercise. Read-only diagnosis of
local WO-003850 found draft lifecycle, no typed assignee and linked Repair Packet28.
The original UI hid manual fields behind capture recovery and displayed disabled
submission without explaining these separate native workflow requirements.
Manual fields also retained an old optimistic token after the user's own report
submission/approval; canonical completion then refused a stale version.

Implemented, with no migration:

- `CloseoutPreparation` names the current state and next native readiness action.
  Standalone transitions are deliberate, versioned, retained and permission-gated;
  blockers remain authoritative. Packet-owned Work instead links its native
  Repair Packet. No automatic safety, assignment, execution or closure bypass.
- With no active capture, the manual account is directly editable. Exactly one
  editor is visible even when legacy review opens. Required fields, source locks,
  readiness and separate reviewer requirements are explained. Existing captures
  and original source decisions are not overwritten.
- After a confirmed own report command, completion uses the current Work token
  and preserves local account fields. `Use submitted account` explicitly restores
  a retained manual account after reload; an active capture cannot be replaced
  by that control. The canonical completion gate still checks the exact approved
  account/evidence. Submission is offered at verification, before approval and
  final completion, avoiding approval invalidation by a later lifecycle change.
- `End test exercise` is an alternative for an administrator merely stopping a
  simulation. It requires current Work/machine scope, optimistic version, a
  reason and explicit confirmation; it retains the native command/event receipt
  and recovery attribution. It resolves only marked `test_alarm_ui` conditions.
  It does **not** complete Work, approve a report, record a repair or establish
  service authority. Its notification says the exercise ended, not that a repair
  was verified. Native notification preferences and local Mailpit still apply.
- The service-report card distinguishes a normal/resolved test exercise from
  other real active equipment alarms. Real abnormal readings are never painted
  green to satisfy a simulated recovery. Pump08 initially had two separate
  acknowledged threshold conditions; ending the UI test left those intact.
  Their legacy development-test provenance was subsequently verified and the
  owner-requested native cleanup is recorded above.
- Shared report-document presentation improves hierarchy, fields, wrapping,
  status/provenance and missing values. **Download PDF** is the primary new export
  in prepared, submitted and frozen report views; legacy exact text and canonical
  JSON exports remain secondary evidence, rather than being relabelled PDF.
- PDF APIs: `POST /api/tasks/work-orders/{id}/closeout/report.pdf` for incomplete
  draft or exact submitted snapshot; `GET /api/tasks/work-orders/{id}/closeout/reports/{report_id}/pdf/`
  for an immutable accepted revision. Current Work read scope, draft version and
  selected source identity are enforced. Rendering is side-effect-free, escaped,
  private/no-store and self-contained: its fetcher rejects network and file URLs.
  Frozen titles, account and full source text come from the selected frozen
  document, not mutable present-day records. The snapshot hash identifies canonical
  source bytes, not the derived PDF. No false PDF filename/MIME wrapping plain text.

Follow-up verification:

- `closeout-fix-sqlite-final/` **159 pass**; `closeout-fix-pg/` **159 pass** on
  disposable PostgreSQL15, with owned container/network removal receipts0.
  Includes eight real PDF render/parse tests, synthetic-only cleanup, native
  report/completion/security, mimic budgets and packet finalization boundaries.
- Full frontend **118 files / 1108 units pass**; all nine TypeScript projects
  and frontend build pass. Lingui adds only43 English messages beyond the earlier78;
  prior catalogue bytes and all non-English `.po` are preserved. Compilation passes.
- Focused Mac Chromium **40/40 pass** on desktop/phone, covering direct manual
  input/PDF media download, one-step lifecycle commands, confirmation/reason for
  ending a test, current completion token after submit/approval, all native final
  update/retry/refusal tests and independent approval/recovery navigation.
  Corrected initial duplicate-editor regression and restored retained-snapshot
  provenance copy before this final green run; no native gate was relaxed.
- Local backend restarted and guarded migration check reports nothing to apply.
  Live Mac desktop **and phone** check passes on5173: manual entry, owning-packet
  link, guarded exercise-end modal, styled preview and real PDF downloads (about
  12KB each). The native phone `Continue to full app` prompt is used, not removed.
  No page errors; Work version and test/real alarm counts remain unchanged.
  Six live screen regions and the PDF first page are visually inspected; local
  files are retained in `closeout-live/`. Earlier phone attempts encountered the
  full-app gate; those partial checks are not described as product failures or
  successful phone acceptance. Applicable hooks and whitespace checks pass.

No owner's test is ended, report submitted, approval issued or Work completed by
the verification. No external email, live SAP, Azure write, commit, push or PR.
The complete owner-operated repair/approval journey is not declared accepted.

Root: `data/devscratch/test-alarm-closeout/` (ignored local artifacts).

- `sqlite-resume/`: **190 tests pass**, no failures/errors, including native
  packets, Work creation, frozen reports, Closeout effects/security, scope,
  query budgets and real local notification/email rendering.
- `postgres-resume/`: **174 tests pass** on disposable PostgreSQL 15, no
  failures/errors. Same focus except the separate 16-case Work-package module.
  The owned container and internal network are removed successfully; cleanup
  receipts are retained. No owner or Azure database is contacted.
- Browser broad run: **76/78 pass**. The two unchanged timestamp cases depended
  on a browser host timezone; explicit `Asia/Riyadh` in this feature's fixture
  config fixes the harness assumption without changing production time logic.
  Final 14-case desktop/phone replay passes both timestamp cases and all new
  flow cases. Thus all **78 distinct selected cases** have passing evidence;
  this is not described as one clean 78-case execution.
- Additional receipt replay passes **4/4** with station, component and signal
  labels preserved after reload/retry, native refresh settled and screenshots
  captured without transient animation. Final report-region follow-up passes
  **8/8**, covering preview, submission, approval, failed refresh and committed
  return. All nine TypeScript projects pass the final recheck.
- The browser inventory includes real WebGL/reference-scene lifecycle tests,
  original pump warning/exact-alarm links, native and synthetic motor attention,
  acknowledgement/recovery/reduced motion, native completion replay/refusal,
  report preview/submission/approval, failed refresh and committed equipment
  return. Linux Chromium fixtures use software WebGL and blocked external reads;
  they do not prove Mac-host or authenticated deployed acceptance.
- Full frontend units: **118 files / 1,108 tests pass**. Lingui extraction adds
  only 78 new English source messages; existing catalogue entries and non-English
  `.po` files are preserved. Compilation passes, including English fallback for
  the new controls in Arabic. No translated-content acceptance is claimed.
- All **16 final primary PNGs** are visually inspected: trigger forms and
  receipts from harness15300, report previews and approved cards from harness15301,
  desktop/phone in light/dark. The final report-region captures are unobstructed;
  earlier transient/occluded captures are not used as primary evidence.
- Applicable repository hooks and `git diff --check` pass. Local logs and curated
  captures are retained in `logs/` and `captures/`; `source-capture-manifest.json`
  identifies the branch, HEAD and SHA-256 of changed sources and evidence.
- The inherited fixed-count tracking validator still fails `Ticket coverage
  differs` (existing R8 additions: 60 backlog rows and 28 contracts versus its
  old 58/26 assumptions). CSV structural checks pass and all prior status,
  disposition and owner values are preserved. No acceptance gate is waived.

Earlier verification failures were corrected rather than hidden: callback mock
scope and revoked-authority expectation; incomplete station fixture identity;
an extra mimic query folded into the existing batch; modal accessible names;
replay selection loss; and screenshot capture beneath the sticky native header.

## Remaining human/integration gates

- Supply and review the real SAP connector, credentials, business mapping and
  acknowledgement/reconciliation policy. The shipped state remains Not configured.
- Exercise the complete write journey with authorized local administrator,
  technician and independent reviewer logins, preserving readiness and physical
  operating authority. Local mail-capture readiness is verified; external SMTP
  delivery, a newly raised alarm's full closeout and human acceptance are not.
- Review this uncommitted patch. No commit, branch merge, push, PR, issue or Azure
  migration is performed for this new task. Parent R6/product acceptance remains
  separate from these source, database and browser-fixture results.
