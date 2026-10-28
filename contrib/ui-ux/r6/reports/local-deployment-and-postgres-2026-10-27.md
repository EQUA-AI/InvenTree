# Latest local deployment and PostgreSQL migration

Subsequent provider change: owner now selects Azure instead of local Qwen.
UI/liveness remain available; AI is not_ready awaiting a private Azure API key.
Current state: [Azure configuration](local-azure-ai-configuration-2026-10-27.md).
The local-model readiness probes below describe the earlier deployment phase.

Date: 27 October 2026. Owner authorizes deploying the merged changes locally
and checking whether local PostgreSQL needs Azure schema updates.

## Running application

Local source is `7e33232c7` plus the preserved, uncommitted recording/AI changes.
The existing recording environment is restored:

- UI: `http://localhost:5173/web/home`, native Vite development server.
- API: localhost8000 TCP relay to combined Django/FastAPI ASGI on8018.
- Backend: `aimms-v5-current-20261027`, mounting the current repository source.
- Recording database: `aimms_v5_take_20261027`, PostgreSQL15.19.
- Existing recording worker, local Mailpit, history feed and ingress are up.
- The main `aimms_azure_copy` worker is restarted after its migration. The
  historical primary backend remains stopped, preserving the recording route.

Actual checks: frontend HTTP200; backend `/health/live` reports alive;
`/health/ai-ready` reports ready. Unauthenticated native session and AI model
requests return401 rather than a missing AI route. Combined ASGI is used;
Django-only runserver would omit the AI endpoints. Both workers have active
native Python processes. Frontend TypeScript and presentation-artifact checks
pass, and the incoming OpenUI dependencies resolve.

Fresh process IDs: Vite13474 and gateway13473. Earlier recorder receipts carry
stale PIDs; they are retained as historical evidence and are not relabelled as
fresh capture approval. New recording preflight must use the live processes.

## Azure and both local databases

Read-only Azure audit uses hostname/certificate-verified TLS1.3 and a read-only
transaction. Before deployment Azure has997applied migrations, while both
local databases have996. The sole missing local migration is:

`voice.0012_voicesession_transport_owner`

It adds `voice_voicesession.transport_owner`, varchar64, NOT NULL, with an empty
database default. The repository migration matches the column already in Azure.
There is no need to replace either local database or copy Azure business data.

Both local databases receive the native source migration. Final independent
read-only verification at20:28:05UTC establishes:

| Database | Applied migrations | Pending / unknown / leftover / conflicts |
| --- | ---: | --- |
| Azure inventree | 997 | Reference set |
| Primary local aimms_azure_copy | 997 | None |
| Recording local aimms_v5_take_20261027 | 997 | None |

Both complete local migration sets exactly match the freshly inspected Azure
set. The new column shape/default also matches. Azure is not modified.

## Preserved data and recovery

| Domain | Primary local before/after | Recording local before/after |
| --- | ---: | ---: |
| Work orders | 1693 | 1700 |
| Repair packets | 17 | 24 |
| Users | 27 | 30 |
| Purchase orders | 21 | 21 |

Case16 remains resolved and linked to WO3992/packet34. Work3992 remains
done/completed; packet34 remains closed. No alarm, work, approval, stock,
procurement, closeout or user-permission mutation is performed for deployment.
These selected invariants are not a claim of a full business-data sync audit.

Private backups are in `data/devscratch/local-deploy-20261027/private/`:

| Archive | Bytes | SHA256 |
| --- | ---: | --- |
| primary-before.dump | 142117004 | 1441bcf98dbd229454792b01275b8e95298ada40d4e127c9df30a316c6397152 |
| recording-before.dump | 142892103 | d85022b3db5abf167b72ee165ef9b35ea6a9113a8e75120e341415d2b33ba95b |

Both pass catalogue and complete payload reads through pg_restore. They are
not restored over the live databases. Environment snapshots and owner secrets
remain private and must not be included in source handoff or publication.

## Runtime corrections

Docker restart assigns the recording PostgreSQL address172.30.0.2 and Mailpit
172.30.0.3, conflicting with the existing exact database guard. The expected
addresses are restored deliberately: PostgreSQL172.30.0.3 and Mailpit172.30.0.2.
The recording DB remains OID150404, cluster7701281817516662829. No identity
guard is weakened. Main local DB remains OID198690 on172.21.0.3.

The normal recording app role correctly refuses table-altering DDL because it
does not own voice_voicesession. Its failed migration rolls back. A one-off
native migration uses the existing database owner only after guarded app boot
and exact database/owner/address/OID/cluster verification. Existing app grants,
table owners and user permissions are unchanged. Primary migration uses its
existing normal management environment. Migration logs retain both outcomes.

## Verification boundary

Native admin browser verification and one read-only station AI question are
in progress. They are separate from the successful source deployment, database
lineage and health probes. Local Qwen model identity passes its native boot
probe; attachment/media RAG remains degraded off because those provider planes
are incomplete. Do not infer grounded manual/attachment answers or complete
V5 script acceptance from AI readiness alone. No new recording, external mail,
Git commit/push, PR, Azure migration or Azure deployment occurs.

Evidence root: `data/devscratch/local-deploy-20261027/`. Source integration
history: [merge ledger](pump08-iot-merge-resolution-2026-10-27.md).
