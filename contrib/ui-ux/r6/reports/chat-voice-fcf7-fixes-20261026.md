# Chat and voice repairs from the fcf7 campaign — 2026-10-26

Baseline: `fcf7f74ecbbb36a509f487fbd8e2f6245649a463` on
`equa/customizations`, in the initially clean `InvenTree-IoT-Refresh` worktree.
Implements the approved source repair plan. The original `InvenTree` worktree
and supplied reports/scripts remain unchanged. No supplied script was treated
as an instruction or executed. No deployment, push, issue, PR or business-data
mutation is included.

## Evidence retained

The supplied `run-fcf7-20261026` campaign has 56 cases and 151 assertions:
37 PASS, 11 PARTIAL, 4 FAIL and 4 blocked. All 140 planned required criteria
are present. Independent read-only compilation reproduces the grades and all
36 recorded hashes across 25 unique files match. All five voice PASS cases
are fixture-only; no human-heard pass is recorded.

Unlike the preceding campaign, these reports establish the candidate identity,
applied `voice.0012`, one worker/replica and exact-candidate traffic. Their
topology preflight passes. The earlier multi-replica failure is historical;
it is not the established cause of these new failures. G02's prior-session
drain was not independently recorded. The ten complete turns prove admission
and scheduling, not audible delivery.

V01's original work-order/BOM answers identify different native records. Later
replays are correct; the original cause remains unproven. The received V07
threshold says **under ten**, and V03 says **part eighty-eight** with unknown
confidence. Digit-only parsing/review missed those forms. V09's inventory switch
was captured by entity focus. C12 additionally contains a global absence claim
alongside a positive four-record series; the old count/scope criteria missed it.

## Implemented contracts

| Area | Source behavior |
| --- | --- |
| Numeric reads and review | Shared positive/negative vectors cover the received word forms, explicit ID fields, quantities, malformed grammar and literal identifiers. Backend read parsing reuses the existing strict reference grammar without changing decisions. Normalization is internal: saved/confirmed transcript spelling and leading-zero IPNs remain untouched. Critical word-number spans hold even with unknown confidence. Ordinary speech retains the existing policy. |
| Exact work status and BOM | Exact supported reads terminate before model planning in text and streaming lookup. Work reauthorizes through the native reader; BOM retains the offered-tool fence and fresh part permission. Requested/invoked/returned IDs must agree. Provider assembly and component PK mismatches fail verification. Native lifecycle and board status remain distinct; BOM IPN, revision, quantity and independent flags are preserved. Unknown flags remain unknown. |
| Identity tracing | Internal evidence retains requested ID, exact invoked arguments, returned ID, native record and native revision when available. A SHA-256 snapshot fingerprints the returned data; content-free verification logs include IDs, revision and snapshot. Unknown revision stays unknown. Native prompt fences stay in evidence and are removed only from the deterministic visible field rendering. |
| Stock completeness | Spoken/typed thresholds use the same full exact-part aggregate, including qualifying zero-stock parts. Spoken lists contain at most three rows and state the full matching count plus omissions. Location answers also state allocated/available amounts when supported. Exact location-scoped allocation/availability is explicitly unverified. |
| Compact identity and location | `part88`, explicit compact IPN fields and `REV-B` qualifiers retain their separate meanings. Conflicting ID/IPN/revision fields are refused. `Room101` resolves only against actual names/paths, including the spaced native name; its digits never become a location PK. Duplicate names ask for a full path or explicit location ID. |
| Filters and keyword scope | Recognized unsupported stock/exclusion predicates return unverified instead of receiving unfiltered model prose. Quarantine semantics are not inferred. Active/current-stock keyword requests page the actual native keyword search, validate complete nonrepeating IDs/counts, then aggregate fresh exact native stock. Category-null keyword hits remain eligible. Authorization is checked again after paging. Incomplete/drifting populations cannot certify zero. Evaluation is bounded at 25,000 keyword records. |
| Category continuity | Recognized inventory category switches bypass entity-focus capture, then use authorized native category stock. Existing machine/task/work-order switches stay distinct. Duplicate stock categories clarify; inventory reads do not mutate durable entity focus. |
| Chart prose | Global absence validation follows the cited series' complete input rows, retrieval and evidence-set siblings, including positive event/group counts omitted by the claim. A selected zero bucket cannot certify global absence. A model contradiction triggers deterministic prose recovery from the same authorized snapshot and full validation, retaining the valid chart. Genuine zero matches over a larger evaluated population remain valid. |
| Reporting and history | Optional named assertion contracts require identity, review, spoken coverage, series/prose and native-history criteria. Missing criteria cannot produce a pass; explicit unverified/blocked entries remain possible. `not_contains` supports negative prose checks. Existing raw references, lanes, original failures and review restrictions remain. Native history DTOs exercise real API cursor exhaustion, order, repeat-read stability and outsider refusal; browser replay verifies unique chronological messages across older pages and reload. |

No dependency, public wire or schema change is required by this repair. The
existing consent, exact decision confirmation, effect authorization, playback
proof, cancellation and transport-ownership contracts remain in force.

## Verification

Retained local logs, native DTOs and captures:
`/tmp/chatvoice-run3-fixes-20261026/`. These are disposable native ORM and
recording-browser checks, not a new deployed campaign.

- **517 backend tests pass** in `backend-final.log`. Final read/identity/display
  follow-up: **51 pass** in `backend-read-display-final.log`; these overlap the
  selection and are not additional unique cases. Includes wrong work/BOM target
  refusal, fresh native stock, duplicate location/category selection, exact IPN
  conflict, spoken coverage and permissions.
- **87 preservation tests pass** in `decision-preservation-final.log`: decision
  grammar/reference pipeline, AG-UI focus and API endpoint compatibility. The
  absent guessed resolver filename in the first invocation collected no tests;
  the corrected explicit selection is the reported run.
- **167 frontend units pass** in `frontend-voice-datasets.log`, covering voice,
  review, decision playback and datasets; **8 dashboard units pass** separately.
  The earlier 68-test run is a subset, not an additional total.
- **57 chart/history browser cases pass with zero skips**, in `chat-browser.log`.
  All 14 native DTO prerequisites were present and explicitly required. This
  includes all formerly unqualified native identity/range/chart cases, full-home
  frame checks and native older-page replay. This does not change historical
  campaign skips into passes. A later focused history rerun scopes order checks
  to the transcript container.
- **19 additional chat presentation/decision browser cases pass**, in
  `chat-preservation.log`; no skips or retries. These complement the chart/history
  selection. The final scoped native-history replay passes in
  `history-browser-final.log` and overlaps its original case.
- **34 voice browser cases pass with zero skips**, in `voice-browser.log`, on
  desktop and emulated Pixel 7. Number-word review confirms exactly once or
  discards with zero turns; restart, no replay, lifecycle, consent and PTT
  regressions also pass. Media and providers are mocked; hearing is unqualified.
- **10 compiler tests pass**. Application and changed-browser TypeScript pass.
  Generated wire is current; `makemigrations voice --check --dry-run` finds no
  drift. Representative mobile review, 320-pixel dark chart and native history
  captures were visually inspected.

Intermediate corrections remain in their logs: the first frontend vectors
exposed missing `part ID`/alphanumeric holds; the first backend run exposed the
inactive test actor and actual event-count field naming; native history cleanup
needed thread deletion before deleting its protected owner. Assertions were
corrected to the real contract and final runs pass. Broader repository tests
are not claimed; the previously documented broker baseline failures are not
reclassified by this selection.

## Release and remaining acceptance

1. Review this source candidate, record its exact commit and image digest, and
   deploy through the existing release procedure. No release occurred here.
2. Record old-session inventory/drain explicitly, migration state and exact
   topology/traffic identity. Preserve the process-owner guard; do not assume
   the healthy supplied campaign proves a later release configuration.
3. Rerun original and matched typed/reviewed-spoken prompts from fresh native
   oracles. Capture requested/invoked/returned IDs and revisions, full keyword
   populations, actual filter arguments, transcript holds and bounded coverage.
   Require chart prose consistency as well as dataset scope/counts.
4. Rerun live C21 with disposable owned threads and native cursors. Local native
   DTO/browser qualification does not replace this live case. E04 stays blocked
   pending approved restricted-role/document/action accounts.
5. Record human-heard answers and physical speaker/headset/Bluetooth routes in
   their own evidence lanes. Playback requested, RTP, fixture PASS and ten
   completed turns do not qualify hearing. Preserve historical failures and
   blocked prerequisites. Parent product/deployment acceptance stays open.

For new manifests, set `require_raw_evidence: true` and choose the applicable
case `assertion_contract` from `exact_record`, `reviewed_transcript`,
`stock_threshold`, `chart_series`, or `older_history`. Populate expected values
from fresh independent native evidence, not earlier assistant answers. Keep
human/physical reviewed acceptance separate from these source contracts.
