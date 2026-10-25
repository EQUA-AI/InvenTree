# Independent frontend review — 24 October 2026

Status: requested independent engineering review gate complete on25 October2026.
The owner requested independent reviews by Mantine &
InvenTree Frontend Engineer (Fable) and Mantine & InvenTree Frontend Engineer
(Gemini), with an honest score of at least 95/100 from each before this review
gate is complete. Earlier implementation and verification remain recorded in
[Performance fixes](performance-test-alarm-fixes.md) and
[test alarm / Closeout](test-alarm-closeout.md). This additional review does not
change human or product acceptance.

## Review scope and evidence

Both reviewers receive the same complete 74-file source/document/test snapshot
from `inventTree-aniket` at `374ac6d247cb7e5ec2369d64077213174311fd0d` plus
uncommitted changes, and 12 rendered screenshots. Each independently assesses
test selection, mimic and chart test states, exact-binding overlays, report
editing/preview/PDF, submission/approval, native closeout and recovery.

Source files carry SHA-256/byte manifests. Each reviewer has its own complete
13-attachment batch because uploaded attachments cannot be reused across
Hyperagent threads. All uploads returned HTTP 200. The local attachment manifest
is `/private/tmp/aimms-independent-review/attachment-manifest.json`.

- Fable thread: `cmvmpn3ez0atu07ad9vgh1op6`.
- Gemini thread: `cmvmpplx00azo07ad620vim8t`.
- Live screenshots: desktop/phone Performance, preparation/report and PDF.
- Fixture screenshots: desktop/phone test selection and approved service report.
- Existing checks are evidence supplied to reviewers, not tests they executed.
- Localhost is inaccessible from their remote runtimes; source and screenshot
  assessment must not be reported as live operator or Safari verification.

## Scoring and completion gate

Each reviewer independently scores task completion (25%), role/journey coverage
(20%), safety/evidence clarity (15%), visual hierarchy/usability/beauty (15%),
runtime reliability (15%), and accessibility/responsiveness (10%). Scores are
expert heuristics and must follow evidence rather than the requested target.

| Reviewer | Score | Gate |
| --- | --- | --- |
| Fable | Final95.15/100 (initial76; expanded85.35; remediation94.45) | PASS without rounding |
| Gemini | Final98.15/100 (initial88.85; remediation97.70) | PASS |

Findings must be reconciled against native source and rendered behavior before
editing. Concrete fixes require focused verification and refreshed screenshots.
Owner alarms remain available for the owner to close; no migration, Azure, SAP,
external email delivery or Git publication is part of this review.

## Expanded exercise final recheck

Both same threads receive the complete final119-file snapshot and17 rendered
captures (18 attachments each;35 cumulative across the three batches). Every
upload is confirmed HTTP200; live and deterministic fixture provenance is
explicit. Gemini verifies119/119 source identities and inspects all captures,
scoring96.30 with weighted criteria97/96/98/95/96/95. The score is an independent
expert source/screenshot assessment, not execution from the remote runtime.
Its residual notes concern transient toast/loading capture timing and the
honestly distinguished post-journey inspector/chart refinements. Fable's final
independent assessment identifies valid machine-scope and accessible-name gaps
plus concrete presentation/liveness issues, scoring85.35. These are being fixed;
the two-reviewer gate is not complete and the scores must not be averaged into
acceptance. Gemini's unit-delta recheck scores96.45. Both will review the scoped
remediation and fresh evidence before final acceptance.

The owner-authorized persisted local alarm13 / WO-003855 now completes through
independent report approval and verified return, with unchanged telemetry,
limits, prior alarms and prior Work. Final equipment condition is normal and
its primary Board card is Done. See [the execution ledger](test-alarm-exercise-values.md).

## Final gate and reconciled residuals

Both independently verify all6 final-polish attachments: complete132-file
source snapshot,3 PNGs,2 actual retained PDFs and7 logs (61 cumulative files
per thread). Exactly10 source/evidence identities change;122 remain identical,
including all backend files. Fable's weighted95.15 and Gemini's98.15 each clear
the gate; no averaging, inflated score or upward rounding is used.

Final-code checks: Performance21, native desktop/phone28,1126 units/122 files,
five types, build17.65s and hooks pass. Backend SQLite177/PostgreSQL177 and
equipment8 carry over unchanged. The standalone receipt now omits absent
segments; the last-visible chip explains its guard on hover/focus/touch and
has an accessible description without disabling focus. Actual approved
submission and frozen completion revision1 PDFs are downloaded read-only and
hash-verified, distinct from the earlier draft. No new alarm or Work mutation.

Fable's narrower residual observations remain useful despite Gemini finding no
defect: optional measured context outside the stable assertive panel notice;
submitted-PDF actor/time attribution, nearly empty identity page and frozen-text
boolean wording; inherited heatmap density. These are nonblocking polish, not
unresolved P0/P1/P2 issues. Current workflow cards/events preserve actor and time
attribution; no unsupported PDF or physical-approval claim is made.

Full final reviews and manifests are retained in
`data/devscratch/test-alarm-closeout/exercise-values/review-evidence/final-polish/`.
These are independent expert source/screenshot/PDF assessments, not tests run
by remote agents, Safari/human/product/Azure acceptance or a security certification.

## Earlier scoped remediation and re-score

Both verify all18 new attachments,132 source identities and12 log identities
(55 cumulative attachments each). Fable confirms every prior P1/P2 fixed but
scores94.45, not rounded to95; Gemini scores97.70. Fable's remaining feedback
requests standalone-receipt separator polish, feedback when a chart must keep
its last visible line, and the actual approved/frozen PDFs rather than only
the earlier draft download. These are implemented/collected on25 October;
focused replay and both final independent confirmations subsequently pass.

Coordinator reconciliation rejects Gemini's stale plural-chip finding: the
current source already uses `plural()`; its cited live critical screenshot
predates that fix. All46 channels are listed, not all eligible:42 were eligible
at inspection. The inspector computes age using the resolved clock and native
signal rows; no separate new display-shift mechanism was introduced there.
The shared-scale fixture places159.5 below historical200, not at the scale top.
The57 distinct browser cases plus2 capture-only replays are not59 unique cases.

Fable's nine acceptance checks are implemented: native Board machine scope,
TEST-aware KPI/mimic accessible names, neutral measured mimic values, history
invalidation/lag explanation, last-visible-series guard, approved-state prompt
and exercise-end reset, one assertive notice, absolute terminal-card assertions
and preserved subtask/other-Work cards, Health summary TEST provenance and copy
corrections. Additional fixes preserve family KPI sensor identity, inspector
age/status, named no-measurement family tests and stable row/memo identities.

Current checks pass: SQLite177; PostgreSQL177; Performance21; native desktop/phone28;
equipment8;1126 units/122 files;five TypeScript projects;build;hooks;local dry-run
migration check. The expanded PostgreSQL replay includes every new backend test. Fresh
settled read-only localhost desktop/phone captures and explicit baseline/final
hashes address the earlier evidence gaps. Both existing reviewer threads receive
the complete corrected sources and new captures; no target score is fabricated.

## Earlier findings and remediation

Both independent reviews verify all 13 attachments and all 74 source hashes.
Their initial reports are retained under the ignored
`data/devscratch/test-alarm-closeout/independent-review/` evidence directory.
Gemini's first turn hit a temporary provider error before its final answer;
resuming the same thread produced its complete report without another audit.

Confirmed fixes preserve measured warning/critical badges alongside exercises,
match notice and chart channel names, show acknowledgement/time and stale/quality
provenance, retain mismatched-unit notices while withholding incompatible plot
lines, and add an accurately named core-temperature KPI for core-only machines.
Visible chart channels govern annotations and axes; equal test values on one
axis share one labelled line. Labels use theme-aware contrast and right anchors;
chart margins and the phone toolbar improve containment.

Trigger success invalidates the signals query immediately, including while
Performance is paused, and offers an explicit next-test action with fresh
confirmation. Report submission disables unchanged retained accounts, labels
changed submissions as resubmissions, and states that another independent
approval is required. The backend refuses identical approved resubmission.
Report preview exposes PDF download above the document. Native preparation
labels are localized and version-conflict recovery is explicit.

Exercise-end commands refuse already-ended tests without another version bump;
notification callback failures are logged without hiding the committed outcome.
Ending a previously reviewed exercise warns that its changed evidence requires
re-approval. The current local database has suppressed/resolved tests rather
than the earlier open test9. Suppression remains distinct from recovery: a new
pending count keeps the explicit end action available for suppressed exercises.
No owner alarm status is changed by the review.

Existing browser runs after remediation: 16 Performance cases and 22 native
desktop/phone workflow cases pass. All 1,118 units and four TypeScript projects
pass. The first disposable backend run passes 43 tests; the final suppressed-test
regression and fresh local captures are still being verified. These are local
or fixture checks, not human/Safari/SAP/product acceptance. Initial browser
attempts exposed a Performance-only fixture without the native header and a
visually hidden Mantine checkbox locator; corrected native/label probes pass.

Some proposed changes were reconciled rather than copied literally: the real
parameter role is `core`, not `stator_core`; synthetic values stay visible in
the default chart scale; pausing history keeps its frozen window; the native
phone control already supplies horizontal overflow and a mask. Existing native
permissions, valid changed-report resubmission, configured thresholds and data
quality rules remain authoritative.
