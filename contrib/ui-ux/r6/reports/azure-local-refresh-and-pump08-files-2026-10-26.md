# Azure-to-local database refresh and Pump08 attachment pull

## Result

Completed locally on 26 October 2026. Primary application:
`http://localhost:5173/web/`, backend `http://localhost:8000/api/`.

- Localhost uses local PostgreSQL, not direct Azure PostgreSQL calls.
- Its active database is now a verified Azure snapshot, captured at
  **2026-10-26 19:36:15 UTC**. It is not a continuous synchronization.
- All ten Cedar Creek Pump08 attachment PDFs are available through the primary
  application's native storage. The original local-only database is preserved.
- Azure database, files, application revisions and equipment are not modified.
- No migration, Git commit, push, deployment, external email or recording occurs.

This is database/media operational verification, not full product, authenticated
UI, physical-equipment, diagnostic-provider or recording-tool acceptance.

## Discovery and snapshot verification

Actual Django connection SQL originally finds local PG15.19 database
`aimms_azure_copy`, OID42032, on the InvenTree Docker network. The Vite5173
proxy targets backend8000. Host5432 belongs to a different application and is
not used for this operation.

Direct Azure access to `epconchat-pg-dev.postgres.database.azure.com/inventree`
uses the existing private credential file and certificate/hostname-verified
TLS1.3. Both audit and dump connections enforce read-only transactions.
Source PG15.19 and dump/restore client PG15.19 match.

| Check | Result |
| --- | --- |
| Non-system tables and all row counts | 298/298 match the exported source snapshot |
| Column definitions | Match |
| Migration records including application timestamps | 996/996 match |
| Index definitions and metadata | 1,506 match after narrowly verified SQL representation normalization |
| Constraints and validation metadata | 1,478 match after the same normalization |
| Encoding and locale | UTF8 / en_US.utf8 match |
| Native migration lineage/history/conflict checks | Zero pending, zero unknown, no conflicts |
| Restored vector engine smoke query | 1,536 dimensions; self cosine distance0 |

Two index definitions and31 check constraints initially differ as SQL text:
casting a constant varchar array to text[] versus casting each constant element
to text. Only that exact literal-array form is normalized. All other catalog
metadata and SQL must match. All30 distinct constant expressions are evaluated
with read-only SQL and prove equal; no constraint is removed, rewritten or
ignored to pass verification. Raw mismatches and the proof are retained.

Azure's vector extension is0.8.2; the existing local installation remains0.8.7.
The real restore, index/constraint checks and native vector query pass. No Azure
extension upgrade or local application migration is performed.

## Active data and preserved original

| Domain | Original local | Azure snapshot / active local |
| --- | ---: | ---: |
| Work orders | 1,568 | 1,693 |
| Purchase orders | 20 | 21 |
| Repair packets | 27 | 17 |
| Users | 29 | 27 |

This is a snapshot replacement, **not a merge**. Local-only repair/demo/users
are retained separately in `aimms_azure_copy_before_20261026t193609z`, still
OID42032. Active `aimms_azure_copy` is the restored database, OID198690.
Existing user IDs are not remapped or combined.

Private mode0700 operation directory:
`data/devscratch/azure-local-refresh-20261026/20261026T193609Z/`.

| Artifact | Bytes | SHA256 |
| --- | ---: | --- |
| `azure-inventree.dump` | 142120861 | `caad9b811c06859e6d86564b6593666ea36b1906d62f08fa50bdc31f3e9ff0ea` |
| `local-before-refresh-201105.dump` | 141065085 | `1ee7edb8ec8cbb6d34a09872f7da33279f5257924de382313ebb3cc53fa0c564` |

Both archives are0600 and pass catalogue plus complete payload readback.
The Azure archive is genuinely restored and checked before promotion. The
original-local archive is not restore-rehearsed; its intact original database
also remains available. An earlier local backup is retained too.

The first export lacks a CA bundle inside the client container; a public trusted
bundle is provided without disabling TLS verification. The first transfer then
hits its15-minute controller limit. Those incomplete files are explicitly
renamed `.dump.incomplete` and never accepted. The final successful export runs
container-side with a longer limit and is copied only after pg_dump exits0.
Temporary credential files are removed; credentials are not printed.

## Local startup handling

The old development command explicitly enables Django-Q `--sync`. Restored
Azure plugin events expose recursive inline event dispatch during startup.
The first promotion fails its health gate and automatically restores the
original database. Native no-sync startup is independently probed successfully.

Primary runtime is recreated with the same image, environment, mounts and
127.0.0.1:8000 port, removing only `--sync`. No production business source is
changed. The original stopped container remains
`aimms-r6-viewer-server-before-20261026t193609z`. A fresh restore is revalidated
before the second promotion; failed startup copies are not promoted.

Native local worker `aimms-local-worker-20261026` supports normal asynchronous
dispatch. Its separate settings restrict it to local `aimms_azure_copy` and
disable scheduler/catch-up/sync. Imported Azure schedules are not started.
The copied source queue is empty before local startup. Mail remains captured
by `aimms-local-mailpit:1025`; no external SMTP is configured by this task.
Native application worker detection and the actual worker configuration pass.

Docker management briefly times out during restart/stop. It recovers without a
Docker Desktop restart or restarting unrelated applications. The primary backend
is restarted and independently verified against the restored database identity.
All four HTTP checks pass:5173,8000,5180,8007. The isolated recording database
retains OID80927 and the original5180/8007 services; it is not refreshed.

Rollback helper is private and fail-closed by default:
`python3 data/devscratch/azure-local-refresh-20261026/rollback.py` prints only
the plan. `--restore-original` explicitly requests the database-name reversal,
preserving the refreshed database separately. It has not been executed after
the successful final promotion. Review local worker/container ownership before
any rollback because the new worker holds connections to the active database.

## Pump08 Azure Files copy

Selected asset is **Cedar Creek Final Effluent Pump Station / Pump08**, asset102,
parent station93. Millbrook's different Pump08/asset115 is not selected.
The database identifies10 direct asset attachments:68 and171–179. They are all
PDFs; there are no thumbnail references for these records.

Source is verified by exact existing file paths and properties:
storage account `epcon0ai0storage`, share `inventree-media`, root-relative
`attachments/assetmachine/102/`. Account keys stay in child-process environment,
never command arguments, reports or persisted credential files.

**Actual primary local destination:**

`/Users/Aniketkumar/IdeaProjects/InvenTree-r6/viewer/data/media/attachments/assetmachine/102/`

This is the host directory mounted as the primary application's media root,
not the main repository's similarly named `data/media` directory.

The10 PDFs total **33,781,410 bytes**. They include the S-Line motor IOM and nine
WEG documents covering synchronous motors, Micatherm insulation, testing,
low-inrush motors, training, insulation assessment, lubricant evaluation,
contaminants and wound-stator preservation.

Verification for every file:

1. Exact asset ownership and recorded database path; no traversal or symlink escape.
2. Azure file size matches the recorded attachment size.
3. HTTPS download with native Azure CLI transfer-content validation enabled.
4. Source ETag/size remain stable across download; PDF signature is present.
5. SHA256 matches staged download, final destination and a fresh native Django
   storage read after database promotion. All10 open successfully.

Azure does not provide a stored full-file MD5 for these objects, and these
attachment rows do not contain a reference-pack SHA256. No such independent
stored-digest verification is claimed. Local/staged/native hashes plus native
transfer validation, TLS, source ETag and sizes are the actual evidence.

Existing different local files would be backed up before scoped replacement;
unrelated media is never overwritten or deleted. All10 selected files were
missing and are copied. No attachment row or remote file is edited.
Private manifest, hashes and readback are under
`data/devscratch/pump08-media-sync-20261026/`.

## Remaining scope

- There are229 other unique missing local media references after this scoped
  copy, compared with239 immediately after the database restore. They are not
  included in the request to pull Pump08 documents.
- Refresh localhost5173 and use the restored database's account if sign-in is
  requested. Pump08's native Attachments tab has10 corresponding rows/files.
- Authenticated browser preview/download is not exercised in this task; native
  application-storage full-file readback is verified, not represented as UI QA.
- Recording permissions, commercial license and manual private-browser login
  gates from the recording-tools setup remain separate and unchanged.
