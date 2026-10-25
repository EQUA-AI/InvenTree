# Chat drawer and OpenUI — EQUA integration, 25 October 2026

The synchronized `equa/customizations` baseline is `bab82119077256ecb5130b538266fe153bc3e778`. This change integrates the completed chat/OpenUI work into that baseline, preserving the newer Work, reference, investigation, authorization and voice flows. `IOT-Merge` is not a publication target.

## Delivered behavior

- Session-private RAM drafts and visible attachment queues retain their originating thread. Draft revisions, upload attempts and send admission fence stale callbacks. Failed uploads and target selections expose explicit recovery. Question answers consume only their own admitted message.
- Extracted Mantine header, conversation browser, transcript, composer, message renderer and context bar. Desktop resizing, compact voice controls, keyboard viewport geometry, overlay ownership and guarded focus restoration share the existing chat/voice controllers.
- Optional deterministic OpenUI layouts reference verified canonical attachments. Generation is **enabled by default**. No extra model pass, arbitrary queries, mutations, forms or automatic business actions are introduced. Canonical prose, copy, export and speech remain authoritative.
- Renderer `1.0.1`, CLI `0.6.0` and Zod `4.6.7` are pinned. The lazy renderer disables inspector auto-mounting and observability. A single flat layout is limited to 200 nodes, 100 references and a 64 KiB program; datasets to four historical snapshots, 512 samples each and 64 KiB total.
- Terminal metadata persists with the existing ledger and both transports. Owner history/replay retains the recorded snapshot. Shared readers receive canonical content without owner presentation/datasets. Missing artifacts, invalid metadata, chunk/render errors and disabled presentation preserve the complete canonical fallback.
- Existing thread cleanup distinguishes thread obligations from owner/client erasure namespaces. Unknown outbox kinds continue to fail closed.

The integration retains required-focus normalization, late voice-session fencing, fresh reauthorized focus-echo IDs, captured acknowledgement versions, and signed Work/reference receipts. Work acknowledgement keeps its existing locked composer; other target preflights allow draft edits and cancel stale submissions. The newer return-focus and overlay layout behavior is retained.

## Controls and maintenance

- Backend `AIMMS_FEATURE_CHAT_PRESENTATION=false` (alias `FEATURE_CHAT_PRESENTATION=false`) disables generation, capability advertisement and history projection. Restart the backend after changing the environment. Existing canonical answers and ledger metadata remain intact.
- Frontend `VITE_CHAT_PRESENTATION=false` independently selects canonical rendering at build time.
- Use the repository's Yarn workflow. The existing npm lock is retained; no forced peer resolution is used. Frozen Yarn installation validates the combined OpenUI and existing Three.js dependency graph.
- Run `yarn presentation:generate` after intentional library changes and `yarn presentation:check` before release. Package all files under `ai/core/presentation/generated/` in the backend image. Runtime validation uses `importlib.resources`; no production Node service is needed.
- Regenerate `lib/types/AimmsWire.generated.ts` with `python manage.py generate_wire_contract`, then verify `--check`.
- New messages are appended to the 39 configured catalogs while retaining existing translations. Untranslated messages use English fallback. The unconfigured Indonesian catalog is unchanged.

## Verification ledger

Evidence: `/home/lokesh/.hermes/workspaces/equa-openui-publish-20261025-zo8bip87/`. Each named phase records its command, times, exit code, stdout and stderr. Failed intermediate checks remain available.

| Check | Result |
| --- | --- |
| Focus/draft/attachment/parser/dataset units | 224 passed (`frontend-units-fixed`) |
| Presentation/default-on/localhost auth and ORM | 47 passed (`backend-presentation`), including five real loopback HTTP cases in disposable SQLite |
| Existing turn/analysis/history/focus regressions | 103 passed (`backend-regressions-fixed`) |
| Existing retention regression | 44 passed and 8 subtests (`retention-regressions`) |
| Application and chat browser TypeScript graph | Zero diagnostics in the final application/chat/S1/S8 browser graph (`final-application-browser-types`) |
| Library declarations/build | Passed (`library-build`) |
| Presentation artifacts | Passed (`presentation-artifacts`) |
| Localization | All 39 configured catalogs compile (`locale-compile`) |
| Combined dependencies | Frozen Yarn install passed (`frozen-install-online`) |
| Full chat/voice browser regression | 98 unique cases verified: 95 initial passes plus all three corrected/replayed failures; the focused 10-case run also rechecks modal ownership after autofocus restoration (`chat-browser`, `chat-browser-focused`) |
| Work/reference browser integration | 44 unique desktop/phone cases verified: 39 initial passes plus all five corrected/replayed failures (`work-browser`, `work-browser-corrections`) |
| Enlarged Work composer/resize/focus return | Passed after warm fixture startup (`work-zoom-browser-warm`); the test targets the independently scrolling composer region |
| Final optimized production fixture | 24 passed (`production-browser-final`), covering rich metadata, fallback, both wires, overlays, compact/touch/keyboard geometry and RTL. Two development-specific source/chunk fault injections are covered in the development run |
| Native application production build | Passed with a 6 GiB Node heap (`native-production-build-heap`); the earlier default-heap build exhausted memory. The final fixture independently rebuilds and exercises the final component sources |
| Backend wire artifact drift | Passed (`wire-drift-final`) |
| Style and commit hooks | Scoped Biome/Ruff and Git whitespace checks pass; repository hooks are required for the publication commit |

Tests use isolated copies of the combined EQUA source, local browser fixtures, and disposable backend databases. Browser fixture auth and API responses are mocked; the separate loopback HTTP tests use real signed subjects, users, grants and repository writes. No remote site, live provider or production deployment is part of this qualification.

Production output contains one lazy OpenUI renderer chunk and no inspector/devtools source modules in its 323 application source maps. The isolated uncommitted build reports `commit: null`, `dirty: true`, `ui_contract: 1`; it is qualification evidence, not a clean commit-bound deployment artifact. Existing large-chunk and third-party annotation warnings remain. Desktop rich rendering, light/dark Work answers/refusals and 320/390/768-pixel compact production captures were visually inspected (`screenshots/`).

Intermediate failures were retained and resolved: preserve EQUA's strict required-focus and late-session guards; use fresh reauthorized focus-echo IDs; retain the exact acknowledged focus version; preserve the inline machine refusal; supply complete strict scope and thread-bound upload fixture payloads; restore the composer's native autofocus target. Cold fixture startup timeouts were replayed after warmup. An additional memory/evidence suite attempt required its deployment login setup and is not counted as qualification; its standalone no-login inclusion was removed. Memory modal ownership is covered by the qualified overlay cases.

The original checkout's 365 recorded WIP paths, HEAD, Git status and staged index are preserved, apart from the authorized default flag and current progress-record edits. The old divergent EQUA branch remains separately backed up. Only the reviewed 130 feature/documentation/catalog paths are publication candidates.

## Localhost operation

The development checkout also has the default enabled. Its restart exposed four pre-existing pending migrations: two conversation-focus migrations and two unrelated additive staff/trade migrations. The local database was backed up before applying the existing migrations to restore startup. The unrelated staff source and migrations are excluded from this commit. The localhost API returns HTTP 200 after restart; a fresh settings probe reports generation enabled and packaged artifacts valid. The pre-migration database backup remains outside Git in the evidence directory. Runtime recovery is recorded in `localhost-migration-plan`, `localhost-migrate` and `localhost-asgi-enabled`.

## Release acceptance

Automated localhost evidence does not substitute for human review, physical phone keyboard/microphone/audio checks, a real screen reader, deployment session/CSRF qualification, live provider verification or human translation. These acceptance activities remain open. No PR, issue or production deployment is created. Existing parent R6/R8 deployment and product-acceptance gates remain unchanged.
