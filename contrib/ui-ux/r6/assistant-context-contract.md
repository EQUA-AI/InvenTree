# P02/P33 shared assistant context — implementation slices

**Started:** 19 October 2026 under O07's delegated design/implementation authority. This is a source-backed contract and staged implementation, not a claim that exact work/request focus already exists.

## Existing contracts that must remain distinct

| Contract | Actual source | Boundary |
|---|---|---|
| Actor authorization | Server principal, thread owner/scope hash and native per-record resolvers | Browser hints grant nothing. Revoked/out-of-scope IDs remain denied. |
| Durable analysis scope | `ai/core/analysis/scope.py`, `aichat/services/threads.py`, generated AnalysisScope/ThreadScope types | Asset selection/date/source-class narrowing only. Backend explicitly excludes selected work/documents from this contract. Do not silently add work/request IDs to it. |
| Machine launch hint | AIChatState and native drawer send | Existing positive machine ID/name seed a scope PUT; typed message is not prefixed with invented context. The old failure path warned then sent anyway. |
| Exact work/request/component focus | Not represented by the current machine hint or analysis-scope payload | Needs an explicit supported versioned contract, provider authorization and persisted turn/result linkage before launch can claim this context. Media `work_order_id`, a clarification interrupt ID or source-class allowlist is not a substitute. |
| Turn scope receipt | Native `expected_scope_version`, server version check and atomic stored scope snapshot | Acknowledgement must match the requested thread and canonical scope. A label or HTTP200 alone does not confirm identity. |

## First implementation slice — fail-closed existing machine handoff

1. Validate native GET/PUT scope responses at the network boundary using generated wire types: known schema/modes, canonical IDs/date/source classes, thread identity, version, hash shape and editability.
2. Validate successful PUT acknowledgement against the exact requested canonical scope and the native `expected_version + 1` result. Do not call a mismatched or malformed200 response success.
3. A machine-launched message requires the advertised capability and successful acknowledgement before any typed stream POST. Refusal, missing capability, unknown/malformed acknowledgement or unavailable refresh sends nothing and preserves draft/files/hint.
4. The existing composer can retry. Removing the machine selection is an explicit choice to abandon that launch context; ordinary unhinted chat remains available and still uses the server's current analysis scope.
5. Confirmed hint collapse uses canonical machine IDs, never matching display names. The returned active scope stays server-only, not arbitrary browser/user-setting persistence.
6. Existing thread/account/close guards and native per-turn authorization/version checks remain authoritative. No new assistant, permission bypass, business mutation, generated wire edit or backend model migration in this first slice.

## Remaining P02/P33 work

## Second implementation slice — native Work-focus repository foundation

The retired `scoped_` namespace and deleted scoped-chat tables are not restored. Existing `trusted_context.py` still rejects browser record claims as authority. This slice first adds a separate server-only Work-focus receipt/provider to the existing `ThreadRepository`; it does not yet advertise an HTTP capability or make a Work-launch UI claim.

- Owner-only acknowledgement binds a positive canonical Work ID and its native `lifecycle_version` to the actor, owned thread and server policy hash. Its signed receipt expires after300 seconds. It does not mutate the Work record or analysis scope.
- Candidate resolution reads the current active actor, native Work view role and canonical `tasks.scope.scoped_work_orders` boundary. An explicit Work customer still wins over its machine's client. Cached permissions, assignment, matching labels and machine access never substitute for Work authorization.
- Receipt use rechecks signature/purpose/age/bindings and current role/scope/Work identity inside the existing transaction before creating or replaying a turn. Missing, foreign, stale, tampered and unauthorized candidates share a value-free rejection.
- The immutable identity includes Work ID, native lifecycle version, owning machine ID and hashed canonical maintenance boundary. Lifecycle version is not claimed to version every editable Work field. No request/component/evidence schema is invented.
- `ChatTurn.trusted_context.work_focus_snapshot` stores the verified identity without the receipt. Replaying the same key cannot add, remove or change that focus, even if a caller supplies the same fingerprint. Unfocused turns retain their exact existing stored shape.
- Owner-only saved-turn lookup reauthorizes the current Work before returning the original focus with a truthful stale flag. This supplies a backend identity primitive, not yet a rendered saved-result return journey.

No migration, HTTP/voice turn adapter, model/tool consumer, generated wire or frontend entry/return is changed in this foundation slice. Those consumers must be wired and tested before the feature is advertised; merely stuffing this identity into ordinary browser `context` is explicitly unsupported. Runtime evidence and remaining work are recorded in the execution ledgers.

**Actual validation:**27 new Work-focus cases plus11 native analysis-scope cases pass. Final broader repository replay ran62 cases:59 pass, one lifecycle failure, one lifecycle error and one existing post-pilot-sharing placeholder skip. Both lifecycle failures reproduce in the same isolated environment with only `ThreadRepository` replaced by its HEAD source; no workspace source is reset. The separate normalized-turn pytest island cannot run with the Django runner's settings, and the cached image has no pytest; that compatibility suite is NOT VERIFIED. Ruff check passes all three touched Python files; the two new files format cleanly. Twelve formatter-only edits in the existing repository are identical to HEAD's inherited formatter delta and remain untouched. No new dependency was installed.

## Third implementation slice — transport, turn consumer, result linkage and Work launcher

19 October 2026, under the same delegation. This wires the repository primitive above into the real adapters and the one existing assistant. It is still Work-only focus: no request, component or evidence focus is defined, and the analysis scope is untouched.

**Wire contract (versioned, generated).** `ai/core/analysis/wire.py` adds `WorkFocusIdentity` (`schema_version`, `kind: 'work_order'`, `work_order_id`, `lifecycle_version`, `machine_id`), `WorkFocusAcknowledgeRequest`, `WorkFocusAcknowledgement`, `SavedTurnWorkFocus` and `WORK_FOCUS_ERROR_CODES = ('work_focus_rejected',)`. `generate_wire_contract` emits them into `AimmsWire.generated.ts`; only that section was applied. The hashed maintenance boundary never leaves the server except inside the signed receipt.

| Surface | Contract |
|---|---|
| `POST /threads/{id}/work-focus` | Owner-only. Strict positive integers (`StrictInt`, no `"73"`, `true` or `4.0`). Materializes a client-minted thread as the scope PUT does, then issues the 300-second receipt. Returns the public identity and receipt; every refusal is the same `400 work_focus_rejected`, an unowned thread `404`. Writes nothing to the Work or the analysis scope. |
| `/chat`, `/chat/stream`, `/agui` | Optional `work_focus_receipt` (1–4096 chars; AG-UI `forwardedProps.workFocusReceipt`). A detector (`verify_work_focus`, read-only, no lock) refuses with `400 work_focus_rejected` before any stream or model call; `begin_turn` verifies again under the thread lock. A revocation between the two emits `RUN_ERROR` with `code` and `failure_class` `work_focus_rejected`; no turn exists. |
| Turn | The receipt goes only to `begin_turn` — never into the request fingerprint, trusted context, stored metadata or model input. An unfocused turn keeps its exact existing call and stored shape. Replay compares verified identities, as before. |
| Model input | `TurnRun.work_focus` → `workflow_context["work_focus"]` (ids only). The default lookup rail (wf8) appends a fixed `[work focus]` user-role note naming the Work id and machine id, and when a focused question names no capability ("what is left on this?") reselects on that Work so the maintenance pack is attached instead of a clarifying question. The maintenance tools still re-authorize every read. |
| Result | `canonical_result["work_focus"]` and the assistant message metadata carry the public identity; replay returns it with the original answer. `GET /threads/{id}` projects it per message to the owner only (a grantee sees `null`). |
| Saved focus | `GET /threads/{id}/turns/{turn_id}/work-focus` returns the identity as asked plus `stale`, re-authorized now; absent, revoked or unknown are one `404`. |
| Capability | `capabilities.work_focus = true` on the thread list. The browser sends nothing focused without it. |

**Browser.** `openGlobalAIChatAboutWork` sets a Work-focus hint kept apart from the machine-scope hint (setting one clears the other). The Work page header has an "Ask about this work" button carrying the Work id, the `lifecycle_version` it shows, its label and the current route. On send the drawer acknowledges first (`parseWorkFocusAcknowledgement` accepts only the exact thread, Work and version), then sends with the receipt and clears the hint. Missing capability, a refusal, a malformed or mismatched 200, or a close/switch during the request sends nothing and keeps the draft and attachments; removing the Work is the only way to ask without it. A resend after a scope conflict acknowledges the same Work again instead of resending without it. Both sides of the exchange carry a chip linking to the Work route (`workFocusReturnPath` keeps the launching panel only when the stored path is that same Work; anything else falls back to the Work's default view). On a phone the link closes the drawer.

**Actual validation (this slice):** 41/41 Django Work-focus and scope tests on a fresh scratch database (3 new); 136/136 targeted chat-wire tests (36 new, pytest island in the locally present `aimms-merged-image`, offline); the full island has the same 192 pre-existing non-passing tests before and after (167 of them one broken `aichat.0042` migration dependency in the island settings) and 3,376 passing. Frontend: 35 new parser/route units; 18 new browser cases across desktop and phone, including the full Work → drawer → acknowledgement → answer → link-back journey that returns to the Closeout panel with its unsaved native draft. Full preservation replay and source hashes are recorded in `s1.md` and the slice manifest.

## Fourth slice — how a saved answer's Work stands now

`GET /threads/{id}` adds `work_focus_state` (`current` / `changed` / `unavailable`, generated `WorkFocusState`) to each focused answer for the owner; a grantee and unfocused answers get null. `ThreadRepository.work_focus_states` resolves the answers' turns, re-authorizes each distinct Work once through `authorized_focus` and compares it with the stored snapshot (including the hashed boundary). The browser shows "Changed since this answer" with an explanation, or a grey unlinked chip with "No longer available to you"; unknown states are dropped. Evidence: `data/devscratch/r6-s1/2026-10-19/focus-state/`.

### Remaining integration

- Request, component and evidence focus: no canonical provider or revision contract exists; nothing is advertised for them.
- Voice turns do not carry a Work focus; the voice rail is not part of this contract yet.
- Only the wf8 lookup rail renders the focus for the model. Specialist rails receive `work_focus` in their context but do not yet use it; routing is unchanged.
- `changed` reflects the Work's lifecycle version, machine and boundary; edits that do not bump the lifecycle version are not detected.
- Native draft retention is proven where the Work page stays mounted under the drawer (desktop, and the phone link-back). A Work page that was unmounted in between keeps only what its own native state keeps.
- Acknowledging materializes a client-minted thread even when the Work is then refused, as the scope update does.
- Real login/CSRF/session, deployed adapters and a live model are not exercised; all of the above is isolated synthetic evidence.

The first native-machine slice now has 43 acknowledgement-parser and 6 identity-display unit cases. Browser coverage exercises refused/unavailable/unsupported scope, malformed successful responses, exact version-conflict retry, retained files/draft, explicit discard, repeated/closed sends, canonical reload and late-read protection. Eleven existing native backend scope tests pass in the isolated scratch environment. Full preservation replay and the final source checkpoint are recorded separately in `s1.md`; these tests do not supply the missing exact conversational-focus provider.

- Define exact conversational focus separately from analysis-scope narrowing, using real canonical work/component/request/evidence providers and revisions.
- Authorize and acknowledge the supported focus before send; bind immutable identity to the saved turn and returned result rather than trusting message text or a browser context bag.
- Specify same-thread/new-thread behavior, capability negotiation, reload, stale/revoked identity, selected saved request/evidence and return-panel/draft restoration.
- Exact work/request, multi-round persistence and device/session integration remain unimplemented until their corresponding schema, runtime paths and tests exist. This first safety slice does not finish P02, P33, S0 or S1.
