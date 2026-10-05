# AIMMS MAF migration: feature and authority inventory

## Status and decision ownership

**Status:** Admin-accepted source preservation inventory; full H0 and effective/live qualification remain pending. This is not a statement that every capability is live or that the migration has passed parity.

- Admin/product approver: the requesting administrator.
- Approved source: `equa/customizations` at `05d47269bdd58febafea6f91542ad37b54ed7fdc`.
- Migration branch: `feat/aimms-maf-harness`.
- Migration checkout: `/home/lokesh/Documents/mbpro/InvenTree-MAF-Harness`.
- Staging target: Azure Container App `aimms-dev`, with companion `aimms-dev-worker`, resource group `EpconChat`.
- Evidence capture: October 5, 2026 UTC. The conversation began October 4 in America/Chicago.

**Classification rules:** “Registered/supported” is source evidence, not effective deployment enablement. “Template-on/off” refers only to allowlisted ARM settings. “Source-default-off/on” is the approved commit's default, not the older deployed image's default. “Unresolved” stays unresolved until authenticated capability discovery and admin confirmation. Preserve supported-but-disabled features without activating them. Retired behavior stays retired.

All `ai/core/...` references below are relative to `src/backend/InvenTree/`. Test names refer to `ai/core/tests/` unless a different app/path is stated.

## 1. Registered workflows

The source registry registers the following nine identifiers: eight numbered workflows and `general`. Definitions are in `ai/core/workflows/registry.py:301–454`. These registrations do not prove live routing or availability.

| ID | Existing purpose and source | Current classification | Migration commitment and required evidence |
|---|---|---|---|
| `wf8` | Parts, stock, BOM, locations; `wf8_lookup.py` | Registered/supported; live availability unresolved | First read-only harness rollout. Positive/negative native-role matrix, two-customer scope cases, useful answer and selected-tool parity. |
| `general` | Lookup-based fallback; registry lines 441–454 | Registered/supported; live availability unresolved | Same authority floor as WF8. Preserve fallback, email/PDF tool governance and typed failures, not unrestricted general tools. |
| `wf9` | Cited technical/evidence retrieval; `wf9_rag_retrieval.py` | Registered/supported; corpus/feature enablement unresolved | Scoped sources and citations; no inappropriate conversation replay. `test_wf9_rag_retrieval.py`, retrieval envelope and corpus tests. |
| `wf2` | Sequential BOM/parts compatibility; `wf2_parts_analysis.py` | Registered/supported; tagged orchestrator feedstock | Preserve sequential semantics, user context and per-step tools. No renewed investment in retired parser behavior solely because of an SDK change. |
| `wf3` | Concurrent research; `wf3_research.py` | Registered/supported; tagged orchestrator feedstock | Preserve inherited actor/scope, isolation and aggregate budgets across parallel branches. |
| `wf4` | Governed procurement; `wf4_procurement.py` | Registered/supported; approval required by registry | Preserve application proposals, approver authority, argument binding, execution owner and reconciliation. Test only controlled effects. |
| `wf1` | Complex diagnostics; `wf1_diagnostics.py` | Registered/supported; normalized diagnostics path also requires coverage | Preserve machine/fault context and no cross-fault caching. Preserve public alias `wf1_diagnostics` → `wf1`. Inspect actual diagnostics dispatch, not just this legacy module. |
| `wf7` | Approval-ready repair packet; `wf7_repair_packet.py` | Registered/supported; diagnostic registry path | Preserve packet schema/provenance and separately protected diagnostic-reader paths. No generic inventory write tools. |
| `wf6` | Incoming documents/Document Intelligence; `wf6_documents.py` | Registered/supported; approval required by registry | Preserve extraction, review, source scope and downstream processing/worker ownership. |
| `wf5` | Historical CPQ | **Retired**, not registered | Do not restore. Lingering CPQ intent remains remapped to WF8; DevUI must not expose retired execution. |

### WF1 qualification that supersedes a simplistic reading of the research plan

`agents/factory.py:23–25` retains a WF1 constructor-tool exception. However, `wf1_diagnostics.py:33–36` explicitly defines `LEGACY_DIAGNOSTIC_TOOLS: tuple[Any, ...] = ()`: the legacy constructor sites currently pass an empty set. Complex diagnosis is documented there as intercepted by the normalized turn service and sent through the Foundry adapter and diagnostics registry.

Therefore, do not describe WF1 as currently having a broad static inventory toolset. H1 must cover both the empty legacy path and the actual normalized/diagnostic dispatch, and must not reuse the factory exception for new harness agents. `test_universal_enforcement.py:25–28` identifies WF1/WF7 as non-catalog paths; their separate policy requires explicit verification.

## 2. Cross-cutting feature inventory

| Area | Source/configured evidence | Initial classification | Required preservation or qualification |
|---|---|---|---|
| Non-AI application | `InvenTree/asgi.py:35–84` | Supported | Provider/AI configuration failure cannot take down the main software. |
| Authentication and principal | `ai/core/auth.py:80`, ASGI outer auth mount | Supported | Sessions/signed subjects, inactive users, conflicting identity, CSRF/origin and principal propagation. `test_ai_boundary_auth.py`, `test_trusted_context.py`. |
| Canonical tools and native RBAC | `tools/rbac.py`, `tools/capabilities.py`, `workflows/rbac_run.py:97` | Supported | Filter canonical objects before wrapping; preserve stable IDs and current-user execution checks. Unknown/hidden tools denied. |
| Invocation guard | `tools/invocation_guard.py:170,291,306`; template `wf8,general,wf2,wf3,wf4,wf6` | Supported; runtime enforcement posture requires attestation | Approved source adds mandatory `wf8,general,wf9` floor to configured set. Do not infer floor behavior of an older deployed image. Migrated confidential/effectful paths must be hard-enforced. |
| Customer/resource scope | Template resolvers `tasks.scope.granted_client_scope_resolver` and `repair.diagnostic_scope.single_site_diagnostic_capability_resolver` | Template-configured; authenticated row-level behavior unresolved | Native view grants do not imply all-customer access. Test rows, snippets, counts, citations, media and history before model context. |
| Machine/maintenance/work-order reads | Corresponding template flags are true | Template-on; behavior unresolved | Preserve readers, scope checks and work-order vs audit-history permission distinction. |
| Kanban/work-order mutation | Canonical governed tools and proposal tests | Supported; live capability unresolved | No restoration of direct-ORM mutation shortcuts. Positive writes, denied writes, exact proposal and status read-back. |
| Procurement/email effects | Registry, native mappings and `aichat/services/proposals.py` | Supported; staging effect scope not yet approved | No real customer order or real recipient until approved. View/send permissions remain distinct. |
| Historical/audit access | RBAC mappings and diagnostic tests | Supported | Do not collapse audit access into general maintenance view. |
| Evidence and manual grounding | Template `AIMMS_EVIDENCE_GATE_MODE=shadow`, `AIMMS_MANUAL_GROUNDING_MODE=shadow` | Template-shadow; effective deployed behavior unresolved | Retain evidence/provenance/holdback contracts. Promotion posture requires explicit review; do not claim the staging template already enforces strict grounding. |
| Analytics/router | Source analysis modules; template analysis-router enforcement `0` | Supported, template router-enforcement off | Preserve typed scope, dates/timezones, structured schema and validate-before-display. Do not silently enable a different router. |
| Controlled manuals | WF9, controlled document corpus/search tests | Supported; live corpus unresolved | Preserve applicability, corpus/site filters and source permissions. |
| Attachment RAG | Source feature/config and corpus tests; flags not explicitly set in sampled ARM template | Unresolved live; supported in source | Establish actual ingest/retrieval posture separately. No activation based on index name presence. |
| Media RAG/evidence | Template `FEATURE_MEDIA_EVIDENCE=True`; ingest/retrieval flags not explicitly set | Mixed: evidence template-on; retrieval/ingest unresolved | Preserve PDF/image/video enabled paths, permission scope and worker routing; evidence flag alone is not full RAG enablement. |
| Questions/decision cards | Template `FEATURE_QUESTION_CARDS=True`; question/decision tests | Template-on; authenticated behavior unresolved | Preserve turn binding, server-authored questions, answer ownership, expiration and cancellation. |
| Thread history and replay | `memory/maf_adapter/_replay.py`; template rail replay `1` | Supported, replay template-on | One append/replay/compaction owner; only intended roles/sites replay. No second harness-owned writer. |
| Compaction | `aichat/tasks.py:689,901`; template compaction true and shadow true | Supported, template-shadow | Preserve scope/redaction/CAS semantics and worker state. Saved messages alone do not prove durable execution. |
| Semantic memory/consent | Source memory services/proposals; permission `aichat.write_memory` | Live read/write/consumer posture unresolved | Preserve supported functionality and consent/retention; never activate a dark consumer or default file memory. |
| Thread sharing | `config.py:738`, source default false; sampled template has no explicit flag | Source-default-off; live unresolved | Preserve audited read-only grants if enabled. Sharing never transfers creator tool permissions. |
| Voice reads | `rbac_run.py:45`, `config.py:478` default true | Supported; effective live posture unresolved | Preserve modality fences, principal/session ownership and read-only lookup path. |
| Voice confirmed writes | `config.py:490–525`, `voice/write_gate.py`, `voice/action_policy.py` | Supported; approved source confirmation default **true**; live posture unresolved | Ordinary lookup remains fenced. Preserve any enabled exact-confirmation/governed write lane; no new autonomous write authority. Recheck RBAC, payload and executor ownership. This is not a request to disable an existing supported feature. |
| Voice providers/live transport | Voice modules and tests; deployment flags/version not attested | Unresolved live | Preserve actual selected provider, wire ordering, interruption and reconnect behavior. Unit stubs do not establish microphone/transport parity. |
| Streaming/events | Template tool events `1`, token streaming `0`; `test_agui_translation.py`, `test_token_streaming.py` | Tool events template-on; token stream template-off | Preserve AIMMS ordering, IDs, completion/errors and evidence buffering. Do not forward arbitrary vendor events. |
| Spec-clean AG-UI route | `config.py:458`, source default false; no explicit sampled flag | Source-default-off; live unresolved | Preserve existing enabled transport, keep optional route dark unless approved. Frontend AG-UI dependency presence is not route enablement. |
| Budgets/rate/admission/stop | Template quota profiles `1`; enforcement/stop flags not explicitly set | Supported; effective enforcement unresolved | Aggregate parent/child/resume budgets, shared-cache outage policy, cancellation and pilot stop. Resolve isolated DB-test bootstrap blocker. |
| Telemetry/privacy | Invocation span comments, tracing allowlist tests | Supported | No prompts, raw tool args/results, tokens, customer payload or provider exception leakage to unapproved exporters. |
| Workers/ingestion | `aichat/services/memory_worker.py`, email dispatch and source jobs; companion ARM app running | Template worker present; jobs/queue health unresolved | Attest web/worker runtime and queue ownership, retries, scheduled consumers and optional memory worker separately. |
| Model policy | Template `gpt-5.1`, fast `gpt-4.1`, embeddings `text-embedding-3-small`, API `2024-10-21` | Template-configured; provider compatibility untested | Keep provider/deployments/model policy fixed during harness comparison. Preserve client authentication distinctions and request limits. |
| Release verification | `contrib/container/AIMMS-release-checks.md` | Existing documented contract | Authenticated build/capability/proposal checks remain mandatory, plus writes, workers and recovery not covered by GET checks. |

## 3. Capability posture for the new harness

**Initial policy proposal:** existing authorized business tools only. New shell/code execution, generic HTTP/SQL, filesystem access, autonomous file memory, web search, skills, new delegation, standing approvals and background loops remain off. Existing approved domain research tools are not removed merely because default general web search is off.

Planning/todos may be admitted later as bounded, scoped application run state. Restart-safe execution, cancellation and approval resume are required H4–H7 capabilities, not permission to activate the vendor's background/shell defaults.

An actual offline `create_harness_agent` probe on core `1.20.0` showed zero tools with optional features disabled, but still an `InMemoryHistoryProvider` (`load_messages/store_inputs/store_outputs=true`). Supplying `context_providers=[]` does not by itself eliminate its history owner. H2/H3 must explicitly supply the application history bridge and prove exactly-once replay/append before traffic.

## 4. Admin decisions and remaining H0 gates

The admin accepted the original inventory as written and selected the staging-test planning boundary on October 5, 2026 UTC. `admin-planning-acceptance.json` in the durable evidence directory binds that decision to the approved document SHA-256 and a frozen copy. The preservation rows above are unchanged.

1. **Accepted:** preserve the inventory as written, including disabled posture, retired WF5 and explicit unresolved live behavior. No silent deletions or revivals.
2. **Accepted planning boundary:** isolated test customer, reversible fixtures, and **mocked** outbound email/orders. Existing real customer data and recipients remain untouched. Specific live fixtures, deployments, account/RBAC changes, real-customer writes and external integrations still require separate approval; this decision does not execute or authorize those changes.
3. Resolve live enablement through authenticated capabilities and approved configuration inspection. If a supported flag is not explicitly present, do not choose its value from the approved source's defaults while the deployed commit differs.
4. Establish permitted non-admin fixtures: authorized reader, limited/no-scope reader, buyer/approver, email-view-only, audit-denied, and two distinct customer scopes. An admin-only demo is insufficient authorization evidence.
5. Set performance/cost regression thresholds against measured current workflows before canary. No improvement claim is made in this inventory.

H0 is not closed by document acceptance alone: the dispatch census, baseline test-bootstrap qualification, authenticated feature posture and approved controlled-effect fixtures must also be established. See `EXECUTION_PLAN.md` and `WORK_PACKAGES.md`.
