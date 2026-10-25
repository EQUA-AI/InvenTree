# Test exercise values and full closeout journey — 24 October 2026

Status: implemented; the native local write journey and final chart checks pass.
Final independent quality re-scoring is in progress.
The owner expanded the current
review task to require all real pump parameters in the test selector, visible
critical test values across mimic/chart/KPI/board, and an actual local
trigger-to-closeout exercise with restoration of every view.

## Requirements and boundaries

- List the selected pump's actual mapped parameters, with current value, unit,
  configured bounds and quality; make unsupported choices understandable.
- Persist a real local synthetic exercise. Show its clearly labelled TEST
  values above the configured range throughout the affected views, rather than
  relying solely on a separate horizontal reference line.
- Keep measured observations, historian data and engineering limits intact and
  available as measured context. Do not depict fabricated past readings or OT
  actuation. A test presentation is separate from a plant measurement.
- Run the local native create/report/independent-approval/completion/verification
  journey using authorized actors and actual readiness. Do not bypass Repair
  Packet ownership or physical safety gates, invent a SAP acknowledgement, or
  record an actual repair that was not performed.
- Close only the exercise created for this verification; leave the owner's
  other alarms and Work unchanged. Verify restoration to actual measured states,
  including genuine unknown/warning/critical states where applicable.
- Fable and Gemini independently assess the final frontend with the requested
  honest 95+/100 gate. Their earlier initial scores are not final acceptance.

## Starting evidence

The preceding [independent review](frontend-independent-review.md) produced
16 passing Performance browser cases, 22 passing native desktop/phone workflow
cases, 1,118 passing units, four TypeScript projects, a production build, and
44 passing backend cases in a disposable SQLite database. Fresh local Mac
Chromium desktop/phone report previews pass; all signal rows, the owner's test9
and Work3850 remain unchanged. Phone full-app confirmation is handled on both
hard navigations. Earlier partial capture failures are retained separately.

The local owner has since suppressed tests9/10/12 and resolved test11. No
active open/acknowledged test was present at the 18:43 UTC inspection. The prior
critical-state screenshots therefore remain fixture or earlier-live evidence;
the new owner-authorized exercise will supply the fresh live critical journey.

## Delegation and ledger

Fable thread `cmvmpn3ez0atu07ad9vgh1op6` owns native selection and effective-value
UX/composition recommendations. Gemini thread `cmvmpplx00azo07ad620vim8t` owns
the backend contract and safe multi-actor native journey recommendations. Each
receives a complete new four-attachment batch: the 91-file source/evidence
snapshot plus current phone preview and light/dark critical-chart captures.
Their original independent reviews remain in the same threads. Both verified all
91 source hashes and the four recheck attachments. Recommendations are preserved
with the local evidence. The implementation retains critical-only exercise
severity rather than adding an unrequested warning workflow. Contrary to one
specialist assumption, corrective Work creation defaults to a Repair Packet;
the software-only exercise explicitly selects native Inspection and no packet.

## Implementation

- Pump07 exposes all46 active mapped parameters,42 currently eligible. Numeric
  channels show current value, quality, tag and bounds; existing tests and
  non-numeric/status channels remain listed with an explicit disabled reason.
- An editable finite test value must exceed usable measured data and every
  configured upper bound. Channels without a critical limit require an explicit
  value and disclose exercise severity, not an invented engineering threshold.
  The value participates in command replay identity.
- KPI figures, comparison points, timed trend traces and mimic callouts show
  labelled TEST values plus measured context. Test traces start at the trigger,
  never in earlier history. Measured lines keep their original timestamp rows;
  injected exercise boundary rows must not manufacture measured gaps.
- Exact-binding overlays disappear on recovery. Trigger/return invalidate native
  health, mimic, equipment, dashboard and Board reads. Board cards show current
  equipment condition and a TEST marker without rewriting historical priority.
- No new model, migration, dependency, native assistant or header replacement.
- The equipment position inspector uses the same exact-binding annotations,
  presenting the TEST value with measured context and retaining real exceptions.
  Final fixture captures verify this post-journey visual refinement.
- Completion atomically moves the Work's primary Board tracking card to the
  terminal column; independent subtask cards retain their own stage. The
  persisted journey exposed that the older native service updated only the
  Work's legacy stage. Its already-completed tracking card was reconciled
  through the authorized native Board control, not a database status override.

## Persisted local journey

One exercise, anomaly13, on Pump07 Motor Core RTD2 (binding1408): TEST159.5°C,
configured critical145°C, measured40.8°C. Actual browser writes create
WO-003855 as a software-only Inspection. A native typed assignment and each
readiness-gated lifecycle transition precede report submission. Actor1 submits
and completes; dedicated local reviewer39 independently approves and verifies.
The report explicitly disclaims physical work and plant operating authority.

The browser checks critical KPI, trend, comparison graph, mimic, equipment and
Board, downloads a real13,444-byte PDF, completes Work and verifies automatic
equipment return. The same six views restore to measured state; the core KPI
returns to the measured family maximum41.9°C. Alarm13 is resolved and Work3855
is completed. Unknown and unconfigured components remain honest, not forced green.
Desktop critical/restored captures and a restored phone capture are retained.

The local email inbox captures both trigger and recovery messages to
lokesh@equa.work and admin@demo.inventree.org. These are Mailpit captures, not
external delivery. Native in-app trigger/recovery records are retained. The
audit compares SHA256 identities for all signal rows, all bindings, every prior
alarm and every prior Work; all are unchanged. Temporary sessions are removed
and the dedicated reviewer is disabled, preserving its approval audit identity.

Partial attempts are retained separately: a missing test-user profile, native
checkbox/option locators and asynchronous report-control waits were corrected
without another alarm or duplicate Work. A notification evidence query initially
used the wrong generic relation and the Mailpit subject classifier initially
missed its server prefix; neither was an application delivery failure.

## Verification and evidence

- SQLite124 backend cases pass; PostgreSQL147 cases pass on throwawaypg15,
  including health conditions, summaries, mimic, targets, reports, PDF and Board.
- Native desktop/phone browser24 cases pass, including all-parameter selection,
  disabled status channels, editable unconfigured values, retry, PDF and return.
- Performance16 final cases pass, including original measured-curve segment
  counts and full plot visibility in phone fullscreen. Earlier locator-only
  failures and premature reads are retained rather than counted as green.
- Equipment8 light/dark desktop/phone cases pass; final synthetic inspector
  captures/recovery4-case replay passes. The additional equipment backend54
  cases pass. Final primary-card completion/closeout/inspector/target64-case
  replay passes; its explicit independent-subtask preservation assertion is
  included in the last repeat.
- Frontend1121 units/121 files, five TypeScript projects (app/lib/S2/S3/S6),
  production build and whole-feature hooks pass. Fresh read-only desktop/phone
  submitted-report captures pass with the PDF action fully visible.
- Dry-run makemigrations --check reports no changes. The prior1,119,032-byte
  English catalogue snapshot remains an exact prefix of the final1,126,041-byte
  catalogue; this review adds missing entries only and preserves other locales.
- Evidence: data/devscratch/test-alarm-closeout/exercise-values/ contains
  disposable backend manifests/logs, live journey.json, audit.json, PDF and PNGs.

## Independent review remediation

Fable's expanded review scores85.35 and identifies valid machine-scope,
accessible-name, measured-colour and history/visibility issues. Gemini scores
96.45 on the prior unit-alias delta; that does not waive Fable's findings or
constitute acceptance of the newer remediation.

- Board condition reads now use the native machine-authority gate before both
  batching and projection. A shared customer Work with a foreign-client machine
  yields no condition until the reader receives that client's grant. A missing
  request fails closed.
- KPI and mimic accessible names include TEST and measured context. Only the
  synthetic value/verdict is critical red; genuine measured text is neutral.
  Family KPIs omit another sensor's sparkline/delta; same-channel test markers
  use the actual shared scale instead of a fixed top coordinate.
- Trigger/end invalidate history as well as current signals. A test newer than
  the frozen history window explains the missing trace until Refresh. Derived
  visibility cannot hide the last measured line and its dependent TEST line.
- One stable assertive panel notice is complemented by polite chart notices.
  Approved reports no longer request a fresh reviewer's empty submission draft;
  successful exercise-end clears its reason and confirmation state.
- Health summary exposes and labels synthetic provenance. Family badges retain
  named test values when measurements are unusable; unit/plural/limits copy,
  endpoint-only dots and row identities are corrected. Inspector observations
  show their actual age and the test's status/time.
- SQLite177 and PostgreSQL177, Performance21, native desktop/phone28 and
  equipment8 pass. Units1126/122 files, five types, build and hooks pass. The
  expanded final PostgreSQL replay includes every new backend test. Its earlier clock-default regression and
  test-reader typo are fixed; failed runs remain separate from acceptance.
- Fresh settled localhost desktop/phone reads show restored41.9°C, populated
  history, no test overlay, Done/Normal Board and accessible submitted-report
  PDF download. Sanitized baseline and final SHA256 values are exported in
  live/audit.json; telemetry, bindings and prior alarms/Work remain identical.
- Four additional missing English messages are appended; the previous
  1,126,041-byte catalogue remains an exact prefix of the current1,126,960 bytes.
  Other locale catalogues remain unchanged.

On25 October both reviewers verify the132-file snapshot/18 attachments. Fable
confirms all prior P1/P2 issues fixed but scores94.45, below the gate; Gemini
scores97.70. Final standalone-receipt/last-line-tooltip polish is implemented.
Native read-only downloads now retain the actual approved submitted PDF
(16,610 bytes) and immutable completion revision1 PDF (18,422 bytes), separate
from the earlier draft PDF. Both downloads leave telemetry, alarms and Work
unchanged; sanitized hashes are in settled-read/final-read.json. Final-code
Performance21 and native28 replay pass;1126 units, five types, build17.65s and
hooks pass. Backend SQLite177/PostgreSQL177 and equipment8 are unchanged green.

Both independent final re-scores pass without rounding: Fable95.15/100 and
Gemini98.15/100. Each verifies the complete132-file snapshot, final6-file batch
and61 cumulative attachments. This completes the requested local software
exercise and independent engineering review gate, not human, Safari, Azure
deployment or product acceptance. Nonblocking PDF/summary/heatmap polish remains
recorded in the review ledger; no parent acceptance status changes.

No migration, Azure change, Git publication, external email or live SAP write is
authorized by this local exercise request. Local email is captured in Mailpit.
