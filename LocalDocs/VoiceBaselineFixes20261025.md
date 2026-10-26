# Voice baseline remediation

This change addresses the completed voice baseline supplied on October 25, 2026
(the run timestamps use October 26 UTC). The baseline files remain unchanged.
Implementation started on `fix/attachment-exact-target`, HEAD
`a2d4896d38f7e3cae8b74673900fb145d3c2920c`, with existing uncommitted changes.
The tested deployment advertised source `4db9cdf73d51ee074037469e81d7b1d18a46b17f`;
local verification is not a retest of that deployed image.

## Speech delivery and consecutive turns

Azure Voice Live WebRTC sends `response.created` and `response.done` to the
browser data channel, not the backend control WebSocket. Waiting for them only
on the control socket makes replacement speech exhaust its acknowledgement
bound and close the channel, which also strands subsequent input.

The browser now serializes bounded, content-free lifecycle observations to
`POST /voice/sessions/{id}/lifecycle`. Session ownership and the voice feature
gate are checked before accessing an existing dispatcher. Reports cannot mint
a dispatcher or channel. Unknown, stale and foreign response IDs cannot
complete the current output slot. Queued reports are discarded on session end.

These reports settle internal scheduling as `observed`, rather than verified
`completed`. They carry no transcript, audio,
decision ID, spoken text or confirmation. Client observations never certify
persisted playback completion or invoke the decision/turn pipeline. Ordinary
utterances remain `requested` unless verified through a separate delivery path.
Provider-socket events retain their existing verified path. Single-dispatch
ownership, cancellation fences, exact-text hashing and business idempotency
remain in place.

The slower-presentation rate is now extracted from the internal response
payload and applied through `session.update`; `_aimms_rate` is never sent to
Azure. Dispatcher failure diagnostics include opaque coordinates and a stable
reason, without response content.

Reference: [Microsoft's WebRTC event-routing contract](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-webrtc#event-routing).

## Shared business answers

- Current part totals and location summaries use native, actor-authorized ORM
  reads with `StockItem.IN_STOCK_FILTER`, an exact part/revision, and explicit
  location narrowing. Template rollups are not added to variant stock.
- On-hand, allocations and available quantities are separate. Location-scoped
  availability is left unknown rather than reusing global allocations.
- Counts and totals are aggregated before the bounded location/detail display.
  Truncation remains explicit. Highest-stock queries use a server aggregate
  over the complete selected population and return ties.
- Live component stock queries explicitly request current stock and exclude
  descendant variants. BOM retrieval reads every page and refuses repeated or
  incomplete pages.
- BOM identity stays on the requested assembly/revision. Native availability
  and `can_build` fields survive projection. The `inherited` flag is distinct
  from which assembly defines a line.
- `buildable_quantity` is native-only. `arithmetic_buildable_quantity` is an
  explicitly limited on-hand estimate, sums repeated component requirements,
  and remains unknown for unresolved required components. Repeated component
  lookups are cached within the request.
- Text and voice receive the same stock/source constraints. Previous messages
  establish the subject, not current quantities. The existing ambiguous-symptom
  diagnostic refusal/clarification checks remain covered by regression tests.

No model or schema migration is required. The internal tool payload gains
scope/coverage fields and separates arithmetic buildability from native values.
Consumers must not interpret an unknown native buildability as zero.

## Target resolution

Numeric work-order searches select the primary key within the authorized
queryset, rather than matching digits in unrelated titles. Spoken identifier
slots reuse the existing number parser. Canonical references retain their text
search path. Machine aliases such as “number two” and “No. 2” produce bounded
full-name candidates inside the existing authorized population. They do not
normalize serials or remove leading zeros. Plain numeric part/work-order/serial
identifiers now trigger transcript review before structured use.

## Original workspace verification

Focused backend checks use `ai.core.tests.settings`, disposable SQLite and
recording transports. The existing native startup helper recognizes Django's
`test` argv token, so the runner bootstraps Django under that token before
calling pytest. This is component/ORM verification, not native server startup
or migration qualification. Fixture writes suppress unrelated pricing and
notification jobs and do not use the developer's business database or Redis.

The backend selection covers native grounding, owner-only lifecycle reports,
ten consecutive real route turns with browser-only observations, cancellation
and replacement, speech dispatch, provider allowances, timing, diagnostic
honesty, BOM paging and tool selection. Frontend unit checks cover the complete
voice directory, critical terms and decision playback. Browser checks use the
existing recording-only fixture on desktop and Pixel 7 emulation, including a
ten-turn lifecycle regression and screenshots.

The browser fixture now activates Lingui before importing components, reusing
the existing test bootstrap. It also mocks the new lifecycle endpoint. These
fixture repairs are distinct from application behavior.

| Check | Result |
| --- | --- |
| Focused backend selection, final source | 227 passed, 0 failed |
| Frontend voice directory, critical terms and decision playback | 143 passed, 0 failed |
| Voice session browser suite, desktop and Pixel 7 | 24 passed, 0 failed |
| Pixel 7 rerun after adding the fixture's mobile viewport | 12 passed, 0 failed |
| TypeScript `tsc --noEmit` | Passed |
| Ruff on changed Python sources and tests | Passed |
| Biome 1.9.4 on changed TypeScript sources and tests | Passed |
| `git diff --check` | Passed |

The backend runner reports one pytest warning about the pre-imported `anyio`
module's assertion rewriting. It does not report test failures. No test here
claims human-heard speech or acoustic latency. Desktop and mobile screenshots
were inspected; the fixture now declares its viewport so phone emulation uses
the actual CSS width rather than a scaled desktop layout.

The exact backend selection is `test_voice_baseline_grounding`,
`test_voice_lifecycle_observations`, `test_voice_speech_dispatch`,
`test_voice_route_speech_integration`, `test_voice_route_boundary_regressions`,
`test_voice_gateway`, `test_voice_tts_allowance`, `test_voice_prompts`,
`test_voice_progress`, `test_voice_playback_reconcile`, `test_voice_timing`,
`test_diagnostics_honesty`, `test_stock_summary`, `test_lookup_answer_contract`,
`test_live_inventory_provider`, `test_bom_client_params`,
`test_wf8_voice_readonly`, and `test_maintenance_capability_selection` under
`src/backend/InvenTree/ai/core/tests/`.

Backend execution uses the existing dev container with working directory
`/home/inventree/src/backend/InvenTree/ai`,
`PYTHONPATH=/home/inventree/src/backend:/home/inventree/src/backend/InvenTree`,
`DJANGO_SETTINGS_MODULE=ai.core.tests.settings`, and the dev venv interpreter.
The bootstrap below takes normal pytest arguments (including the exact files
above and `-c pyproject.toml -q`):

```python
import sys
args = sys.argv[1:]
sys.argv = ["pytest", "test"]
import django
django.setup()
sys.argv = ["pytest"]
import pytest
raise SystemExit(pytest.main(args))
```

Frontend commands, run from `src/frontend/`:

```sh
node node_modules/vitest/vitest.mjs run src/components/ai/voice src/components/ai/voiceCriticalTerms.test.ts src/components/ai/decisionPlayback.test.ts
node node_modules/typescript/bin/tsc --noEmit
node node_modules/@playwright/test/cli.js test -c playwright.voice.config.ts pui_voice_session.spec.ts
```

## Integration on equa/customizations

The fixes were ported into the existing clean worktree for
`equa/customizations`, based on
`fc58212ffc26375b61c566bf214c48015fd1e061`. This branch was 379 commits ahead
of the original workspace. Only the remediation delta was applied; newer
lock-independent cancellation, playback audit results, focus preflight,
catalogue search, and record authorization were retained. The original dirty
workspace and its staged changes were left intact.

This branch has retired the demo provider setting. Both public part-stock tools
therefore call the authorized native summary directly, with an additional
regression that exercises both entry points. Its existing slower-rate fix and
stronger wire-order checks were retained.

Target-branch verification uses the same 18 backend modules listed above:
229 tests passed, with the existing anyio assertion-rewrite warning. The voice
frontend selection passed 148 tests. TypeScript and all applicable repository
pre-commit hooks passed. All 24 desktop and Pixel 7 browser checks passed
against a separate Vite server serving this exact worktree on port 5174; both
consecutive-turn screenshots were inspected. Backend checks used managed CPython 3.12 with the dev
venv dependencies and the target worktree first on PYTHONPATH; the existing
isolated settings keep all database/configuration state in disposable scratch
storage. No business database was used.

## Deployment and acceptance still required

No deployment or feature flag change was performed. For the read-only baseline
retest, retain `FEATURE_VOICE_WRITE_CONFIRMATION=false` and
`FEATURE_VOICE_READONLY_TOOLS=true`. Deploy backend and frontend together so the
relay endpoint and client agree. Verify that all HTTP requests for a live voice
session reach the process owning its provider channel; the existing channel
registry is process-local. This change does not add cross-replica routing.

Rerun the original matrix against the identified new image. Establish real
final-answer audibility before scoring interruption, repeat, paging, slower
playback and short versions. Compare matched typed/voice questions with
independent native truth. Measure first final-answer audio separately from
status cues, provider completion and visible-result latency.

Physical microphones, headphones/speakers, human listening and acoustic latency
remain unqualified by mocked browser tests. Document-page and source-conflict
cases require independently reviewed fixtures. Restricted actors, business
actions and network/load injection require their original isolated fixtures.
Disabled guided procedures are not enabled by these repairs. Acceptance and
human review remain pending; do not convert blocked cases to passes.
