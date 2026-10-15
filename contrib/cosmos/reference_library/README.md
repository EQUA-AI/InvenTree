# AIMMS Demo Data Final: where each part of the pack goes

The pack lives outside the repo (`AIMMS Demo Data Final/`, research cutoff
2026-10-05, 4,184 files). Reviewed on 2026-10-15 from `START_HERE.html`
and every page it links to. Nothing in it is approved for an installed
machine. Its own rules apply throughout: a library role is not an installed
asset, a candidate part is not an approved spare, and public retrieval is
not a licence to reproduce OEM documents (`CURRENTNESS_AND_USE_LIMITS.md`,
`RIGHTS_AND_INDEXING_REVIEW.md`).

## What the pack does not answer

The pack mentions no Millbrook, Maple Grove, Pine Hollow or BHEL equipment,
and no `PUMP5_SPEED` tag. (A full-text search of the pack found only one
unrelated OSHA page.) None of the open plant questions in
`PLANT_INPUT_REQUEST.md`, `THRESHOLDS.md` or `STANDARDS_BASELINE.md` is
answered by it. These questions stay open:

- the 474 rpm question;
- the RTD schedule;
- the winding and core limits;
- the field voltage;
- the mimic sign-off.

LIB-P49 ("WEG motor-order RTDs") is a Cedar Creek reference role, not the
real plant's RTD schedule.

## Section by section

| Pack section | Destination in AIMMS | Status |
|---|---|---|
| `00_Start_Here` | Package index and integrity records. Keep with the pack. | Not uploaded |
| `01_Equipment_Manuals_and_Drawings`: the 50 workbook-selected documents (Attachment Plan `CC-DOC-01..50`) | Link-only attachment, either on the family part `CC-REF-Pnn` (pump-train roles, linked to all 14 pumps through `MachinePart`) or on the support machine with serial `CC-REF-Pnn-01` | Command below. 22 linked on Azure (attachments 37-58), 28 held; files stored on the sandbox only |
| `01_…`: the other ~2,300 OEM objects | The same targets, once someone picks which ones to add. `contrib/cosmos/devtools/build_reference_upload_plan.py` classifies every file (upload, hold, skip, non-English, superseded) | Not done; needs selection and a rights decision |
| `01_…/90_Held_Originals_Format_Review`, Val-Matic files (373 `HOLD_PROTECTED…`) | Nowhere until the Val-Matic company-use basis is established (`EXT-RIGHTS-VALMATIC`) or the file is repaired | Held |
| `02_Compliance_and_Reporting` | No compliance model yet (Import Sequence step 16). Never attach regulatory text to a machine | Held |
| `03_External_Measured_Data` (KAIST, OscGrid, UCI, BSM) | Not Cedar Creek data. At most analytics test fixtures, under their own licences (AC16/AC17) | Not uploaded |
| `04_Maintenance_and_Procurement`: `supplier_offers.csv` | One SKU observation: RS `70936921` for Phoenix 2904602 (`CC-CAT-P23-2904602`), dated 2026-10-05, backorder, noncontractual. It is one of the workbook's 6 "NO_VERIFIED_SUPPLIER_OFFER" rows | Not entered. A dated observation does not verify a live offer. The other 5 offers have no data in the pack |
| `04_…`: maintenance guidance (`MNT-*`, NASA/DOE/FIST) | Reference for the 94 PM plans. File or link on the station machine `CC-STATION` | Not done; PM plans are still manual |
| `04_…`: supplier register and captures | Company / contact / address fields for the 30 workbook companies | Not done |
| `05_InvenTree_Reference_Schemas` | Developer reference only (InvenTree 1.3 API schema 477) | Not uploaded |
| `06_…/AIMMS_acceptance_cases.csv` (25 cases) | AI evaluation items (`ai/core/evals/golden`), once the reference documents are in the retrieval corpus | Not done |
| `06_…`: engineering review, variant decisions, part facts | Provenance for machine profile and part parameters, marked TARGET or candidate | Not done |
| `90_Provenance_and_Historical_Reviews` | Audit trail; keep unchanged | Not uploaded |

Not in the pack at all: the 84 pump-train rows (`CC-ATT-PTnn-01..06`). These
are:

- nameplate photos;
- GA excerpts;
- valve layouts;
- lockout diagrams;
- applicable IOM pages.

They must come from the plant and are never generated. The 12 staff accounts
and the 10 repair packets are not in the pack either.

## The reference-library command

`manage.py attach_reference_library <workbook> --pack <pack root> --review
contrib/cosmos/reference_library/attachment_review.json`
(`assets/reference_library.py`). It has the same three modes as
`populate_from_workbook`:

- `--plan` (default);
- `--dry-run`;
- `--apply`, which needs `--backup-ref` and, for a remote host,
  `--allow-remote`.

It creates link-only attachments to the OEM source URL. Each attachment's
metadata records:

- the pack SHA-256 and relative path;
- the workbook hash;
- the approval status `NOT_APPROVED_FOR_SELECTED_BUILD`.

By default no bytes are copied. Link-only attachments are never fetched or
indexed by the attachment-RAG pipeline (`structural_skip_reason` →
`link_only`). The command checks each pack file's presence and manifest
size. Replaying it is a no-op.

`--with-files` follows the owner's decision of 2026-10-15 to store the OEM
documents as files:

- It stores the pack file on the attachment and keeps the source link.
- It re-checks the file's SHA-256 against the pack manifest when planning and
  again just before writing.
- An existing link-only attachment gains its file (action `add_file`) rather
  than getting a duplicate.
- Files above the `INVENTREE_UPLOAD_MAX_SIZE` setting (default 10 MB) stay
  link-only, with a note in the plan.
- The pack's own rights and format holds still apply.
- Against a non-local database it refuses unless `--shared-media` is given.
  The reason is that the file goes to the media storage of the process
  running the command, while the database row is shared.

### The 28 held rows

- **7: no document in the workbook.** P04, P15, P23, P25, P26, P27, P30.
- **10: held by the pack itself.**
  - Seven are Val-Matic rights holds: P05, P06, P07, P09, P10, P13, P50. Four
    of those workbook picks (P07, P09, P10, P50) are only the Val-Matic
    ISO 9001 certificate.
  - Three failed format validation: P02, P08, P41.
- **11: reviewed holds** (`attachment_review.json`). These are title-based
  only; release one by deleting its key once a person has opened the document.
  - P01 points to a third-party mirror.
  - P12, P16, P35, P39 and P42 do not look like the role's manual.
  - P28, P29, P34, P46 and P47 are non-English Endress+Hauser editions. The
    pack also holds the English ones, and those can replace the workbook
    pick.

### Runs on 2026-10-15

**Local sandbox** (`pop-pg`, throwaway):

- Links: the plan showed create=22, held=28. The dry run rolled back, the
  apply created 22, and the replay showed unchanged=22.
- `--with-files`: add_file=15, unchanged=7. The replay showed unchanged=22.
- The 7 not stored exceed the 10 MB limit, so they stay links: CC-DOC-17
  (39 MB), 18 (43 MB), 19 (17 MB), 21 (24 MB), 22 (14 MB), 24 (14 MB) and
  31 (10.9 MB).

**Azure** (`inventree`, links only):

1. Backup `data/backup/InvenTree-db-2026-10-15-073236.psql.bin.gz`. It passes
   `gzip -t` and its sha256 is `6cdae009781f791d94d0c8cdbe243340d0b770ae83fd7686993f14592f08011a`.
2. The plan showed create=22, held=28, the same as the sandbox.
3. The dry run rolled back.
4. `--apply --allow-remote --backup-ref InvenTree-db-2026-10-15-073236.psql.bin.gz`
   created attachments 37 to 58.
5. The replay showed unchanged=22.

The Cedar Creek records sit under client `internal`, as the population left
them. To undo the apply, delete those 22 attachment rows, which are
identified by `metadata.aimms_reference_pack`, or restore the backup.

## Why the files are not on Azure yet

The owner decided on 2026-10-15 to store the files. The only process with
the merged code that writes to the Azure database is the local
`aimms-azure-server` container. Its media root is `/home/inventree/data/media`
on this Mac, and the repository configures no shared media store (no blob or
S3 storage). The deployed dev app still runs the older code. Storing files
from here would leave Azure rows pointing at files that exist only on one
laptop, so the command refuses (`--shared-media` gate).

To finish, configure the deployment's shared media storage (for example
Azure Blob through django-storages). Then run, from a process that uses
that storage:

```
attach_reference_library ... --with-files --dry-run
attach_reference_library ... --with-files --apply --allow-remote --shared-media --backup-ref <backup>
```

The 22 existing links will gain their files (add_file). The 7 large manuals
need `INVENTREE_UPLOAD_MAX_SIZE` raised to at least 45 MB if they are to be
stored too; that is an admin setting and was not changed. With files stored
and the attachment-RAG flags on, the documents become searchable by the AI
as `attachment_uploaded` sources, never as maintenance authority.
