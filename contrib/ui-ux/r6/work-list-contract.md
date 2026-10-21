# P15 native Work-list contract and remaining gates

**Status:** bounded implementation in progress, not deployed or fully accepted. Follows `designprompt1.md`. P02 technical context/provider work and the unfinished P15 criteria remain; delegated owner decisions are recorded separately below.

**19 October ownership update:** the sole frontend/backend owner delegates design/approval to this stream. O08 in owner-approval.md accepts URL/saved-choice precedence and truthful neutral fallback while persona/provider support is missing. The mapping/rule/action items below remain technical design/implementation work, not a request to find another manager/team. This does not accept the incomplete P15 criteria.

## Confirmed source gap and additive provider

The native Board reads `/api/kanban/work-orders/` as an unpaginated array. Changing that envelope or downloading every job to paginate in the browser would either break existing views or leave the new list unbounded. The canonical `/api/tasks/work-orders/` collection is deployment-flagged and does not supply the existing Board's denormalized labels/filter projection.

Add **GET `/api/kanban/work-orders/list/`** as a read-only subclass of the existing scoped native provider. It reuses WorkOrderBoardSerializer, typed assignment, native lifecycle/version, machine identity, scheduling, active-record handling, native role permissions and canonical customer/client filtering. No migration, lifecycle command, new case store or domain-state extension.

- Envelope: native paginated `count`, `results`, `next`, `previous`. Default page25; server cap200. Actor scope precedes filters, counts and pagination.
- Filters: existing typed `assigned_to`, `machine`, `priority`, `lifecycle_status`, `work_order_type` and other inherited Board filters. New UI only advertises the supported subset it consumes.
- Search: actual reference/title/description, machine name, typed owner's username/name and legacy assignee. No model/alias, component, physical location or domain-summary search is promised.
- Default order: newest created first, then owning work PK descending for deterministic ties. This is **not** a Needs-attention readiness/risk rule.
- Existing Board array, card IDs/actions, Calendar/Timeline routes/scheduling and Risk Radar gate remain separate and unchanged. There is no fallback to downloading that unpaginated array if the additive provider is missing or denied.
- POST/PUT/PATCH/DELETE are not offered on the new collection, even to native editors. Backend/session/grant tests must verify this, not infer it from hidden UI controls.

## State and ownership

Work list is a native Maintenance PanelGroup panel at `/maintenance/work-list/`. Keep `tasks-index`, the existing saved panel choice and first Board fallback; do not force a new default before persona mapping is confirmed.

Within the list, explicit supported URL state takes precedence over a saved scope choice, and both take precedence over the first-use persona view. **My work** sends the authenticated typed user ID, never a URL-selected person. **All work** means work authorized by the server, not all tenants. Without an explicit/saved choice, All work is a neutral initial fallback, **not** completed technician/manager-default acceptance.

Only the scope preference is saved locally, keyed by native host key and actor PK. An automatic default is not written as a human-selected All-work preference. This is not a named/shared server-side view, and no arbitrary user-setting key is sent to the backend. Invalid scope/filter values stop the read until recovery rather than silently broadening it.

Search/filter changes reset page; out-of-range pages reconcile only after a successful scoped count. Failure/denial hides cached rows/counts, distinct from a successful empty view. In-flight refetch data is explicitly labeled last-loaded. Native work references link the owning work ID, not a Board-card ID. Native Back restores router history; no fabricated request/evidence context is attached to the assistant.

## Presentation and review

Design direction: a quiet operational workbench, not a competing dashboard. One compact scope/count line, supported filters and an information-dense native table. Separate priority, canonical lifecycle, typed owner, legacy assignee and schedule. Assignment is not acceptance; schedule uses the browser's local timezone. No per-row filled buttons or per-row overview/readiness/asset fetches.

Desktop keeps filters inline and native alternate views visible. Phone keeps search/scope accessible and moves secondary filters into a native modal with count and return focus; the technical table scrolls locally. Review actual light/dark, long-content desktop/phone captures before claiming visual readiness. Synthetic fixture controls/data are not product chrome or real plant records.

## Unresolved P15 acceptance

1. **Implemented 19 October (`tasks/persona.py`, `work_persona.v1`):** the deployment names the groups (`AIMMS_WORK_SUPERVISOR_GROUPS`, `AIMMS_WORK_TECHNICIAN_GROUPS`; empty by default). Supervisor → Needs attention, technician → My work, both → supervisor (decided under O10 delegation: deterministic, no view flip, broader triage queue), none → All work. Native CRUD roles, staff and superuser are not read. The list asks only on first use, waits without selecting or reading, and never saves the starting view as a choice. Remaining: a real deployment must name its groups; until then everyone starts on All work.
2. Confirm the existing supported Needs-attention selection/order rule. Do not invent a numerical risk score, lifecycle shortcut or readiness inference.
3. Map durable next-action, AIMMS-handling/action-result and component context providers before adding those columns. Missing facts are not simulated from board stage, assignment or tool success.
4. Map a neutral Report-observation entry. The current WorkOrderCreateModal creates an audited work package and optional repair packet; renaming that command would not establish a neutral observation contract. Existing create actions remain in their native views.
5. Human/domain review, authenticated UI-to-backend deployment, larger real populations, PostgreSQL locking/concurrency and production permission/storage acceptance remain separate.

Normal-user stored-role/grant/session tests and isolated 50-job browser fixtures validate only their stated boundaries. They do not resolve these owner decisions or make P15/P02 Done.
