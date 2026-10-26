# Chat and voice retest repairs — 2026-10-26

Baseline: `2f86eb18811404485212e5c35b1d7044cdc6df54`,
`equa/customizations`. Implements the approved retest repair plan in the
previously clean `InvenTree-IoT-Refresh` worktree. The original `InvenTree`
worktree and all supplied reports are preserved. Attached scripts were read as
evidence, not executed as instructions. No push, PR, issue, deployment, traffic
change or business-data write is part of this implementation.

## Evidence and diagnosis

The supplied verified campaign contains 52 cases and 149 assertions: chat
13 PASS / 2 PARTIAL / 5 FAIL; voice 4 PASS / 7 PARTIAL / 7 FAIL /
3 human-blocked / 1 unverified; source/reporting 10 PASS. The four passing voice
cases are fixture-only. All 125 planned required assertion IDs are present.
Independent read-only compilation reproduced the grades. All 12 recorded
evidence hashes matched, but the historical compiler did not hash every raw
live source. Original attempts and failures remain historical evidence.

Two causes were confirmed during implementation:

- Read-only Azure logs place requests for the same voice session on different
  pods of `aimms-dev--equa-2f86eb188114`. Three pod names appear in the retained
  campaign window. The deployed app has one Gunicorn worker, Multiple revision
  mode, maximum two replicas and no sticky affinity. Provider event gates,
  dispatchers and turn locks are process-local. Logs include acknowledgement
  and cancel bound failures, followed by closed-channel sends. The old source
  assumption that RTC pins separate HTTP controls was incorrect. This routing
  defect is established; a repaired live campaign is still needed to rule out
  additional provider faults.
- The real dashboard behind chat reproduces transient document overflow.
  React Grid Layout animates a widget's old width for 200 ms after shrinking.
  Captured offending elements are `react-grid-item` widgets and descendants;
  the chat surface fits. Drawer-only fixtures omitted this background. The
  original live trace's easing from width 374 toward 320 is consistent with
  this independently reproduced cause.

## Implemented contracts

| Area | Result |
| --- | --- |
| Voice ownership | An additive private `transport_owner` field is claimed atomically before provider SDP effects. Another process receives `409 VOICE_SESSION_OWNER_MISMATCH` before turn admission, lifecycle reconciliation or provider creation. Actor/scope checks still run first. Status and explicit End remain accessible to the owner from another replica. The process token is not exposed to clients. |
| Voice recovery | Closed speech channels are explicit in answer, prompt, presentation and lifecycle responses. The browser releases media and requires a user restart, preserving an already completed answer. Ownership failures use the same recovery. Neither path resubmits a turn or supplies decision assent. Ordinary failed speech on a healthy channel still permits subsequent input. Dispatch logs include sanitized exception class and correlation IDs. |
| Exact stock | Narrow exact-part/revision stock questions read native current-stock evidence before model/tool-pack selection. Text and streaming output use the same deterministic formatter, including location coverage and consistency checks. Conflicting IDs/revisions remain unresolved. Historical rows, variants and template rollups do not replace exact current stock. Extra unsupported filters remain on the existing planning path. |
| Quantity comparisons | Typed `lt/lte/gt/gte/eq` predicates apply to the full native per-part aggregate, coalescing missing stock to zero before comparison. Native decimal thresholds retain their received precision. Legacy filter conflicts and non-finite/negative quantities are rejected. Results preserve exact part, IPN/revision and display coverage. |
| Machine scope | `machine24`, `machine#24`, `machine #24` and existing spaced forms share the trusted target resolver. Names, model numbers, part IDs and malformed ID suffixes do not broaden scope. Native workflow tests exercise all three target forms, full populations, explicit scope conflict, revoked access and replay. |
| Catalog | Complete explicit keyword and first-page requests read parts with native count, returned-count and has-more metadata. Narration describes that exact scope. A duplicate category name/path asks for an explicit native category ID/path before reading parts. Unknown additional filters are not discarded. |
| Supplied labels | Recognized `using/from ... data only` declarations without a colon are separated from the first supplied label. Unrecognized chart-instruction prefixes remain ambiguous. Values and synthetic provenance are preserved. |
| Mobile overflow | Horizontal overflow is clipped at the dashboard grid that owns the animated widgets. Application/body overflow is not globally masked. The real Header and DashboardLayout can be mounted behind chat in the recording fixture. Resize samples retain offending element geometry. |
| Reporting | New manifests opt into required raw source references for every live attempt. Source/attempt/project matching, raw pointer availability and file hashes are checked. Missing raw evidence cannot create a pass; original failed observations still win. Output includes per-lane assertion counts and fixture-only case labels. Legacy reports remain readable. |

All native reads retain current actor permission checks. Consent, identifier
review, exact confirmation, cancellation fences, decision playback proof and
business-action authority are unchanged. No new dependency or rollout flag.
The new ownership field **does require a migration**, discovered necessary
after the replica diagnosis. Its database default supports an older ORM writer.

## Verification ledger

Retained local evidence: `/tmp/chatvoice-run2-fixes-20261026/`. These are source,
native disposable ORM and recording-browser checks, not a new deployed campaign.

- **447 backend tests passed**, in `backend-qualified.log` (one existing anyio
  assertion-rewrite warning).
  The saved selection now includes native ownership/migration and visualization
  retest modules. The actual additive migration operation was reversed and
  reapplied on disposable native tables; preexisting rows and writes from the
  previous model receive the empty DB default. This is not a fresh full-graph
  or PostgreSQL migration acceptance claim.
- **199 frontend tests passed** across 19 files, including request rejection,
  closed-channel capture shutdown, preserving the result callback, no turn
  replay, ten healthy-channel failed-speech turns, decision playback/reducer,
  timing, labels, datasets and dashboard layout behavior.
- **12 repeated full dashboard/chart tests passed**: two chart types, both
  themes, three runs, 24 viewport transitions and 720 immediate frames. No
  frame widened the document or clipped the drawer, Send control or SVG.
  The pre-fix native dashboard check failed with the responsible widget bounds.
  The broader chat run returned **94 PASS / 1 FAIL**. Its failed active-voice
  case saw four drawer roots during fixture hot updates made while that run
  was active. Three clean repeats pass without a product or locator change;
  this supports hot-update interference as the cause. All **95 unique chat
  cases** are qualified after the corrective replay; do not report the first
  run as clean. See `chat-browser-final.log` and `chat-browser-recovered.log`.
- Voice recording browsers initially returned **29 PASS / 1 FAIL**. The new
  failed desktop check matched both the visible restart paragraph and its
  accessibility announcement; the product had correctly ended capture. The
  locator was scoped to the visible paragraph. All **four** new restart cases
  then passed on desktop and emulated Pixel 7, each proving one HTTP turn, one
  session creation and no decision action. All 30 unique cases are locally
  qualified after this corrective replay. These use mocked media, not hearing.
- **8 report compiler tests and 2 routing guard tests passed**. The read-only
  guard rejects the actual sanitized Azure configuration, as expected. It
  accepts a single worker/replica or Single revision HTTP cookie affinity and
  rejects multi-worker and split-traffic combinations.
- Application and changed-browser **TypeScript passed**; generated wire is
  up to date and `makemigrations voice --check --dry-run` detects no drift.
  All applicable changed-file repository hooks and manual Biome checks pass.
  Representative 320-pixel light and 390-pixel dark charts and the mobile
  restart notice were visually inspected. The task-owned Vite server was
  stopped after verification; other servers were left alone.
  The existing broad Playwright project's two TS6305 build-reference failures
  remain separately disclosed; focused checking does not turn that project
  into a pass.
- R6's strict validator still rejects extension rows with `Ticket coverage
  differs`. The unchanged 58-ticket / 95-screen / 26-contract core passes in
  an isolated copy. Extension CSV shape, unique IDs and provider paths pass
  separate checks; parent status and the validator were not weakened.

Earlier attempts are retained: an unsupported Django `eq` lookup (corrected to
`exact`); repeated fixture machine names (made unique); a migrated test module
mixed with the schema-bootstrap island (excluded in favor of the established
real-route integration); management commands launched from the wrong import
directory; incomplete initial wire optional-field generation; and an initial
report test that set its manifest flag on a case. The first 444-test run had two
fixture contract failures: the lifecycle response now includes recovery fields,
and a nameless placeholder tool did not meet the stable-name contract. Fixtures
were corrected without weakening product checks. Original failures remain in
their logs. No historical report was promoted based on a passing replay.

## Release and acceptance

The current Azure app has **not** been changed and still fails the routing
guard. Use the [live voice release procedure](../../../container/AIMMS-release-checks.md#live-voice-routing):
migrate first, drain existing sessions, keep one worker, and deploy the exact
reviewed candidate with maximum one replica under the current Multiple revision
strategy. Multiple replicas require Single revision mode plus HTTP sticky
affinity, which is a distinct release-strategy decision. Roll back code with
the additive column retained until no process needs it.

After release, verify matching frontend/backend SHA and image digest, then use
a new campaign directory with strict raw evidence references. Repeat the exact
reported stock, zero-stock, compact machine, category ambiguity, pagination,
label and full-home resize cases against independent native truth. Repeat ten
distinct useful voice turns and controls with paired lifecycle IDs. A human
must record what was actually heard on the intended microphone, speaker,
headset and mobile routes. Provider scheduling, recording media and source
passes cannot satisfy human/physical acceptance. Parent R6, release and product
acceptance stay open.
