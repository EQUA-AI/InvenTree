# AI/OpenUI test-result fixes — 25 October 2026

The reproducible code defects in `ai-openui-live-test-results-20261025.json`
and `ai-openui-visual-test-results-20261025.json` are repaired on
`equa/customizations`, based on `f3baa865f88f`. Verification below runs on
localhost and disposable data. This is source qualification, not a new Azure
deployment or a production acceptance certificate.

## Findings and repairs

| Finding | Evidence and repair |
| --- | --- |
| Six live turns failed before useful tool execution | The exact deployed SDK/Python image reproduces two invalid proposal-tool schemas. Its annotation parser treats bare `Literal` values as annotation descriptions. Both proposal tools now use described `Annotated[Literal[...], ...]` parameters; their enum validation and write fences remain enforced. All 102 catalog tool schemas serialize in the installed SDK. |
| Read-only safety phrases selected the specialist path | All six failed prompts include negative effect language. The broker now recognizes explicit prohibitions and bounded coordinated actions, including “do not query, create, modify, approve, or send” and “without changing”. Separate positive effects, contrasts and “do not forget to create” still select the specialist. Selection continues to expose READ-effect tools only. |
| Correct machine answers produced no canonical entity/card | Ordinary chat's manifest depended on optional voice-diagnosis roots. Typed IDs actually observed in tool results now become bounded candidates for fresh authorization through the existing machine/Work readers. Labels come from those readers. Old diagnostic machine/Work roots cannot restore a freshly denied candidate. Generic part/supplier PKs and model prose do not become machine references. |
| A long request for the “selected machine” lost its acknowledged identity | The short-pronoun recognizer rejected this detailed request. Selected-kind references now carry the recorded, reauthorized focus identity through routing. Missing, revoked or mismatched focus clarifies; competing numeric references keep their original routing input. Verified Work targets retain their exact machine/Work identity. |
| Trend generation was dark; the default month window contained 13 buckets | The existing router and evidence gate are independent of the OpenUI display flag. The real enforced trend path is now exercised locally. Its default selects twelve calendar buckets, including the partial current month. Supported “last/past N months” windows remain bounded to 36 months; recorded analysis-scope dates retain priority. Huge integers produce a bounded limitation. |
| The local OpenUI parser test could not load its package | The shared host dependency directory was stale. An isolated frontend uses the already qualified frozen dependency graph, with `@openuidev/react-lang@1.0.1`; the parser and all 224 focused frontend tests pass. No lockfile or dependency upgrade is needed. |

The SDK compatibility investigation used the installed release implementation,
not an assumption that current framework documentation matched this older pin.
Current [Microsoft tool documentation/source](https://github.com/microsoft/agent-framework/blob/main/python/packages/core/agent_framework/_tools.py)
also supports explicit typed inputs; the release regression directly exercises
the installed `AIFunction` schema builder and enum validation. An SDK upgrade
would affect every workflow and is unnecessary for this repair.

## Verification ledger

Private local evidence directory:
`/home/lokesh/.hermes/workspaces/ai-openui-fixes-20261025-0rp0c5q4/`.
The input reports and earlier private deployment diagnostics are preserved.
No customer records, live-provider requests or cloud resources were changed.

| Check | Result |
| --- | --- |
| Release-runtime backend regression | **2,266 passed; 15 failed** (`final-backend.log`). Python 3.14.7, Pydantic 2.13.4, agent-framework-core 1.0.0b251120, image digest `sha256:462e471dc7803e92226ee91fa435c23105602afbe85c35651c11b55c00d9dfa1`. |
| Attribution of the 15 failures | All 15 `TestSourceOwnershipStructuralRegressions` cases reproduce with the unchanged baseline broker (`baseline-classifier.log`). They concern manual-source qualifier grammar, not the reported safety phrases or the repaired SDK schemas. The broad suite is **not** recorded as passing. |
| Final focused check after input-bound hardening | **156 passed** (`final-focus.log`): installed SDK schemas and write fences; read-only/mixed-effect prompts; selected focus; calendar windows including a 5,000-digit requested month count. |
| Exact reported prompt replay | All **11** original prompts select the read path, with zero specialist misroutes and zero selected-tool schema failures (`reported-prompts-replay.json`). Offline selector/schema replay only; no tool bodies or provider requests execute. |
| Actual machine tool → manifest → presentation → HTTP history | Six loopback cases pass inside the broad run. Real SDK invocation/context, capability middleware, ordinary user with native Work-view rules, client grants, ORM reader, signed HTTP subjects and durable repository writes. No hand-built entity manifest or injected diagnostic roots. Shared/stranger/revoked/disabled projections and transcript retention remain covered. |
| Actual trend → evidence gate → presentation → persisted replay | Passes inside the broad run. Two authorized Work rows and one foreign-client row; twelve buckets; sum exactly two; real zeros; plant timezone; date interval/as-of coverage; evidence pass; live presentation event; immutable idempotent replay. Only the optional model organizer is replaced with the deterministic fallback. |
| Frontend units | **224 passed** (`frontend-units.log`), including parser, datasets, focus, drafts, attachments and admission. |
| Optimized localhost browser fixture | **24 passed** (`browser-regression.log`): both stream dialects, history, gaps, copy, canonical fallback, overlays, touch/keyboard viewport, RTL and geometry. |
| Controlled renderer failures | **2 passed** against development source (`browser-fallback.log`): actual component throw and failed lazy-module load retain canonical content and facts. |
| Backend-produced persisted trend in the optimized renderer | **4 passed** (`generated-browser-final.log`): 390/1440 px, actual light/dark themes asserted, all twelve rows, eleven real zeros, count two, clock and interval coverage. The browser consumes the exact backend-produced snapshot; its auth/API shell is mocked. |
| Build and catalogs | Optimized fixture build and all configured catalog compilation pass. Existing large-chunk/third-party annotation warnings remain. No frontend production source changes were required. |
| Changed-file checks | Ruff formatting/lint, whitespace, spelling, hardcoded-secret scan and applicable repository hooks pass (`prek2.log`; final check recorded separately). |

Intermediate failures remain in the evidence directory: the coordinated-negation
grammar initially overextended a `without` clause across a comma and was corrected
before the final backend run. Lightweight test fixtures were updated to provide
real native role permissions, telemetry presentation settings, turn `extras`, and
stored rather than public focus snapshots. A legacy entity test now asks for
details instead of triggering the separate “tell me about” focus-switch flow.
An initial supplementary browser script used an unsupported theme query parameter;
the final run emulates and asserts the actual system color scheme. Final light/dark
phone/desktop screenshots were visually inspected. These corrections are not
silently counted as original passes.

## Repeat the critical checks

Use the release Python/SDK image and a disposable database. Add pytest,
pytest-asyncio and pytest-socket to a separate QA path, not the runtime dependency
set. From `src/backend/InvenTree`, set
`DJANGO_SETTINGS_MODULE=ai.core.tests.settings` and include both that directory
and `src/backend` in `PYTHONPATH`. Run pytest with `-p no:django`,
`--disable-socket --allow-unix-socket`, and allow only loopback hosts for the HTTP
tests. The exact broad command is retained in `run-final.py` in the evidence
directory. Critical test modules:

- `test_tool_schema_compatibility.py`: all real SDK schemas, both enums and both write fences.
- `test_read_only_prompt_selection.py`: reported negative-action shapes and mixed positive-effect controls.
- `test_presentation_localhost.py`: real reader/middleware/manifest, signed HTTP history, sharing, revocation and retention.
- `test_openui_trend_integration.py`: real scoped analytics, evidence validation, twelve-bucket presentation and persisted replay. Optional `AIMMS_TEST_PRESENTATION_ARTIFACT_DIR` exports fixture-only canonical JSON for browser comparison.
- `test_focus_commands.py`, `test_focus_turn_flow.py`, `test_entity_manifest.py`, `test_analysis_snapshot.py`: binding, authorization, typed IDs and calendar bounds.

Frontend: use the frozen Yarn lock, then run `yarn test:unit` for the chat/state
modules and the existing `playwright.chat.config.ts` presentation/workspace/overlay
cases. Source-module fault injection cases run against Vite development; the other
24 cases run against the optimized fixture. Do not label those browser API mocks
as authenticated live-provider tests.

## Remaining release work

The existing analysis rollout settings are preserved. Display enabled alone does
not produce a chart from unvalidated prose. The concrete **trend-only** profile
qualified by the integration test is:

```dotenv
FEATURE_AI_ANALYSIS_ROUTER_ENFORCE=true
AIMMS_EVIDENCE_GATE_MODE=enforce
AIMMS_ANALYSIS_INTENT_HOLDBACK=record_retrieval,manual_fact,source_inventory,fleet_aggregate,manual_wo_comparison
```

This profile routes only the qualified trend intent through the evidence-enforced
executor and retains the legacy routes for other intent families. Review the
existing shadow-soak evidence before deployment; retain both rollback switches.
No global defaults or live Azure settings were flipped as part of these fixes.

The reports do not establish an authorized Cedar Creek Houston dataset. Its
absence must continue to return an honest unavailable/no-match answer; no records
were seeded or substituted. Ordinary/scoped/other-client behavior is now covered
with disposable automated users, but the deployed test-account matrix, real
provider replay of the eleven prompts, approved site ground truth, physical phone
keyboard/audio and screen-reader acceptance still require their actual
environment/resources. The 15 baseline classifier failures remain separately
identified. No blanket production-ready claim, issue or PR is made.

The original dirty checkout and its staged index are preserved. `IOT-Merge` is
unchanged. All implementation changes are confined to the EQUA worktree.
Owned fixture servers on ports 15291/15292 are stopped; the pre-existing localhost
app/Vite on 8000/5173 remain running. The original index file still hashes to
`10dafdf8cd35b4b2eaed83dbe7646fcc6cb1587a1f0469502c6f37edff86d484`.
