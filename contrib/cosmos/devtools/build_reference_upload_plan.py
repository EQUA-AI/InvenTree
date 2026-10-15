"""Map every file of the "AIMMS Demo Data Final" package to its place in AIMMS.

Read-only. Touches no database and copies no file. It reads the package
(``00_Start_Here/SOURCE_CATALOG.csv``, the supplier register and the folder
tree) and the Cedar Creek population workbook (``Attachment Plan``,
``Machine Records``, ``Machine Part Links``, ``Parts Catalogue``) and writes:

- ``reference_upload_plan.csv``: one row per package file with its
  destination (model type + workbook key), decision and reason.
- ``attachment_plan_resolved.csv``: the workbook's 134 Attachment Plan rows
  (Import Sequence step 15) resolved to paths in the new package by sha256,
  with a better-matching candidate where the workbook choice is weak.
- ``summary.json``: counts per section, decision and target.

Usage::

    python3 contrib/cosmos/devtools/build_reference_upload_plan.py \
        --package "/path/to/AIMMS Demo Data Final" \
        --workbook data/demo/cedar-creek/2026-10-12.1/input/workbook.xlsx \
        --out data/devscratch/cedar-creek/reference-upload-plan

Destinations follow how the app stores and indexes files:

- ``part`` / ``assetmachine`` attachments are the only owners the attachment
  RAG pipeline ingests (``aichat/services/attachment_ingestion.py``,
  ``ALLOWED_MODEL_TYPES``); PDFs, DOCX, MD and TXT are indexed there, CAD,
  archives and HTML are not.
- Pump-train library roles go on the family Part ``CC-REF-Pxx``: it is linked
  to all 14 pumps through ``MachinePart``, so the client scope and the pump
  pages both reach it.
- Support-equipment roles go on the support machine ``CC-SUPPORT-Pxx-01``:
  those machines have no ``MachinePart`` link to ``CC-REF-Pxx``, so a part
  attachment would fall back to the ``internal`` client and stay invisible to
  the Cedar Creek client.
- Supplier captures go on the ``company``; station-wide maintenance guidance
  goes on the station machine ``CC-STATION``.

Nothing in the package is approved for an installed machine. Every row keeps
the package's own use limits; this plan only says where a file belongs.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import re
import sys
from pathlib import Path

try:
    import openpyxl
except ImportError:  # pragma: no cover - a developer tool
    sys.exit('openpyxl is required: pip install openpyxl')

ROLE_RE = re.compile(r'LIB-P(\d\d)')
#: Endress+Hauser document codes: BA02011P + EN + _0925-00 (older: BA486CDE_0111)
EH_RE = re.compile(
    r'^(?P<code>(?:BA|TI|KA|SD|XA|GP|BE|CP|TD|HB|EA|FY|SE|IN|CT)\d{3,5}[A-Z])'
    r'(?P<lang>[A-Z]{2})(?:_(?P<mm>\d\d)(?P<yy>\d\d)(?:-(?P<sub>\d\d))?)?(?:[_.]|$)',
    re.IGNORECASE,
)

DOC_EXT = {'.pdf', '.docx', '.md', '.markdown', '.txt'}
IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tif', '.tiff'}
CAD_EXT = {'.dwg', '.dxf', '.stp', '.step', '.igs', '.iges'}
LINK_EXT = {'.html', '.htm', '.svg'}

KIND_BY_FOLDER = {
    'Manuals': 'manual',
    'Drawings_and_CAD': 'drawing',
    'Technical_Data': 'datasheet',
    'Parts_Lists_and_Service': 'parts_list',
    'Native_Bundles_Do_Not_Run': 'native_bundle',
    'Other_Technical_Files': 'other_technical',
}

#: Engineering-review page images and sources, by the record they came from.
ER_IMAGE_ROLES = {
    'KSB_page24.png': 'LIB-P01',
    'KSB_Gen2_page105.png': 'LIB-P01',
    'VM5HPW.png': 'LIB-P07',
    'IFM_page39.png': 'LIB-P03',
    'Bently_IO18.png': 'LIB-P32',
    'IFM150_RevU__1520e48b70a2.pdf': 'LIB-P03',
}

#: Additional OEM references whose catalogue rows carry no library role;
#: assigned from their titles (see 91_Additional_OEM_References).
NAME_ROLES = (
    ('ksb_omega_', ['LIB-P01']),
    ('basler_decs450_', ['LIB-P03']),
    ('basler_control_transformer_', ['LIB-P03']),
    ('valmatic_control_panels', ['LIB-P07']),
    ('valmatic_oil_accumulators', ['LIB-P50']),
    ('OEM-001', ['LIB-P15', 'LIB-P04']),  # SEL synchronous-motor starting
    ('OEM-002', ['LIB-P26']),  # Cat C32B spec sheet
    ('OEM-003', ['LIB-P15']),  # SEL-710-5 document catalogue
    ('OEM-005', ['LIB-P30']),  # VEGAPULS 6X download catalogue
    ('PHX-P23-', ['LIB-P23']),
    ('GAPC-P43-', ['LIB-P43']),
    ('HYP-CORE-S01', ['LIB-P01']),  # KSB 1384.5 type series booklet pages
    ('HYP-CORE-S03', ['LIB-P02']),  # WEG publication 50019091 pages
    ('HYP-CORE-S06', ['LIB-P32']),  # Bently Nevada 3500/42M
    ('HYP-CORE-S10', ['LIB-P45']),  # Basler IDP-801 manual table 2-1
    ('https_www.epa.gov', ['STATION']),  # EPA energy-conservation fact sheet
)

#: Non-English editions outside the E+H naming scheme.
FOREIGN_RE = re.compile(
    r'(Espa|Fran|Deutsch|Portugu|Italiano|Nederlands|Polski|Svenska|Homolog|Conformidade)'
    r'|_(PT|ES|FR|DE|IT|RU|ZH|JA|KO|PL|NL|SV|CS|TR)\.pdf$',
    re.IGNORECASE,
)

#: Station-wide maintenance guidance (04 originals/supplemental).
MAINTENANCE_PREFIXES = ('MNT-', 'SUPP-NASA-')

#: Rendered OEM pages kept "only for internal review" by the package review;
#: public/UI presentation should use the source link instead.
INTERNAL_REVIEW = (
    'HOLD_INTERNAL_REVIEW',
    'Rendered OEM page for internal review only; attach the source PDF/link instead',
)

PLAN_FIELDS = [
    'section',
    'package_relative_path',
    'title',
    'extension',
    'sha256',
    'library_roles',
    'doc_kind',
    'decision',
    'priority',
    'target_model_type',
    'target_key',
    'target_ui',
    'also_applies_to',
    'rag_effect',
    'reason',
    'package_use_limit',
    'source_url',
    'absolute_path',
]

UI_PATH = {
    'part': 'Parts > Cedar Creek / Reference Equipment > {key} > Attachments',
    'assetmachine': 'Machines > {key} > Attachments',
    'company': 'Companies > {key} > Attachments',
    'manufacturerpart': 'Parts > {key} > Manufacturers',
    'supplierpart': 'Parts > {key} > Suppliers',
}


def read_csv(path: Path) -> list[dict]:
    """Read a UTF-8 CSV (BOM tolerant)."""
    with path.open(encoding='utf-8-sig', newline='') as fh:
        return list(csv.DictReader(fh))


def sheet(book, prefix: str) -> list[dict]:
    """Return a workbook sheet whose title starts with ``prefix`` as dicts."""
    for ws in book.worksheets:
        if ws.title.strip().startswith(prefix):
            rows = list(ws.iter_rows(values_only=True))
            header = [str(h) for h in rows[0]]
            return [dict(zip(header, r, strict=False)) for r in rows[1:] if any(r)]
    raise KeyError(prefix)


def roles_of(row: dict, path: str) -> list[str]:
    """Library roles from the catalogue row, a known file name, or the path."""
    found: set[str] = set()
    for column in ('library_roles', 'library_ids', 'library_id', 'library_role'):
        found.update(re.findall(r'LIB-P\d\d', row.get(column) or ''))
    name = Path(path).name
    for prefix, roles in NAME_ROLES:
        if not found and name.startswith(prefix):
            found.update(roles)
    if not found:
        found.update(f'LIB-P{m}' for m in ROLE_RE.findall(path)[:1])
    return sorted(found)


def eh_key(title: str):
    """(document code, language, sortable revision) for an E+H file name."""
    m = EH_RE.match(title)
    if not m:
        return None
    rev = (int(m['yy'] or 0), int(m['mm'] or 0), int(m['sub'] or 0))
    return m['code'].upper(), m['lang'].upper(), rev


def title_score(title: str) -> int:
    """Lower is a better 'primary manual' candidate."""
    eh = eh_key(title)
    if eh:
        # E+H: BA = operating instructions, TI = technical information.
        return 0 if eh[0].startswith('BA') else 2
    t = title.lower()
    for score, words in enumerate((
        (
            'instruction manual',
            'operating manual',
            'iom',
            'owner',
            'user manual',
            'reference manual',
        ),
        ('manual', 'instruction', 'installation', 'user guide', 'operation'),
        (
            'parts',
            'spare',
            'technical information',
            'technical data',
            'datasheet',
            'data sheet',
        ),
    )):
        if any(w in t for w in words):
            return score
    return 9


class Targets:
    """Where each library role lives in the populated database."""

    def __init__(self, book) -> None:
        """Index the workbook's machines, part links and catalogue."""
        machines = sheet(book, 'Machine Records')
        self.support: dict[str, list[str]] = collections.defaultdict(list)
        for m in machines:
            key = str(m['machine_key'])
            hit = re.match(r'CC-SUPPORT-P(\d\d)-\d\d', key)
            if hit:
                self.support[f'LIB-P{hit[1]}'].append(key)
        links = sheet(book, 'Machine Part Links')
        self.pump_linked = {
            f'LIB-P{m[1]}'
            for m in (re.match(r'CC-REF-P(\d\d)$', str(r['part_ipn'])) for r in links)
            if m
        }
        catalogue = sheet(book, 'Parts Catalogue')
        self.family = {
            f'LIB-P{m[1]}': str(r['ipn'])
            for r in catalogue
            for m in [re.match(r'CC-REF-P(\d\d)$', str(r['ipn']))]
            if m
        }

    def for_role(self, role: str) -> tuple[str, str, str]:
        """(model_type, key, also_applies_to) for one library role."""
        if role == 'STATION':
            return 'assetmachine', 'CC-STATION', ''
        if role in self.pump_linked or role not in self.support:
            return (
                'part',
                self.family.get(role, role.replace('LIB-', 'CC-REF-')),
                ('CC-PUMP-01..14 via MachinePart' if role in self.pump_linked else ''),
            )
        machines = sorted(self.support[role])
        return 'assetmachine', machines[0], ';'.join(machines[1:])


def decide_equipment(
    row: dict, ext: str, kind: str, eh_latest: dict
) -> tuple[str, str]:
    """Decision and reason for one equipment-library file."""
    disposition = row.get('indexing_disposition', '')
    validation = row.get('validation_status', '')
    if disposition.startswith('EXCLUDE'):
        return 'EXCLUDE', disposition
    if disposition.startswith('HOLD_PROTECTED'):
        return 'HOLD_RIGHTS', 'Protected OEM content: company-use rights review first'
    if disposition == 'HOLD_FORMAT_REVIEW' or validation == 'FAILED':
        return (
            'HOLD_FORMAT',
            f'Format validation {validation or "held"}: open and repair before upload',
        )
    if ext == '.zip' or kind == 'native_bundle':
        return (
            'HOLD_NATIVE_BUNDLE',
            'Native/software/CAD archive: do not run; not an attachment',
        )
    name = row.get('title', '')
    file_name = row.get('_file_name', '')
    eh = eh_key(name) or eh_key(file_name)
    if eh:
        code, lang, rev = eh
        if lang != 'EN':
            return (
                'SKIP_NON_ENGLISH',
                f'{code} {lang}: an English edition is the one to upload',
            )
        if eh_latest.get(code) != rev:
            return (
                'SKIP_SUPERSEDED_REVISION',
                f'{code}: a later English revision is in the package',
            )
    if FOREIGN_RE.search(name) or FOREIGN_RE.search(file_name):
        return 'SKIP_NON_ENGLISH', 'Non-English edition'
    if ext in ('.md', '.json', '.csv'):
        return 'NOT_AN_ATTACHMENT', 'Review notes/metadata about the references'
    if ext in LINK_EXT:
        return 'LINK_ONLY', 'Web capture: add as a link attachment to the source URL'
    if ext in CAD_EXT:
        return (
            'UPLOAD_FILE_ONLY',
            'CAD drawing: stored and downloadable, not text-indexed',
        )
    if ext in IMAGE_EXT or ext == '.doc':
        return 'UPLOAD_FILE_ONLY', 'Stored for viewing; not text-indexed'
    if ext in DOC_EXT:
        if validation == 'SIGNATURE_ONLY':
            return (
                'UPLOAD_AFTER_OPEN_CHECK',
                'PDF header only verified: open it once before upload',
            )
        return 'UPLOAD', 'Reference document'
    return 'HOLD_FORMAT', f'Unrecognised format {ext or "(none)"}'


def rag_effect(decision: str, model_type: str, ext: str) -> str:
    """What the attachment pipeline does with the file once uploaded."""
    if not decision.startswith('UPLOAD'):
        return 'none'
    if model_type in ('part', 'assetmachine') and ext in DOC_EXT:
        return 'indexed (attachment_uploaded; never maintenance_authorized)'
    if model_type == 'assetmachine' and ext in IMAGE_EXT:
        return 'media pipeline when AI media flags are on'
    return 'stored only'


def priority(kind: str, sha: str, selected: set[str]) -> str:
    """P1 = workbook selection, P2 = manual/drawing/data/parts list, P3 = rest."""
    if sha in selected:
        return 'P1'
    if kind in ('manual', 'drawing', 'datasheet', 'parts_list'):
        return 'P2'
    return 'P3'


class Suppliers:
    """Resolve a supplier capture (``PROC-KSB-SPARES``) to a company name.

    Prefers the workbook's company (already in the database) and otherwise
    names the supplier-register company to create, prefixed ``NEW:``.
    """

    def __init__(self, suppliers: list[dict], companies: list[str]) -> None:
        """Index the register by source id and by brand token."""
        self.by_source: dict[str, str] = {}
        self.by_token: dict[str, str] = {}
        for s in suppliers:
            for sid in (s.get('source_id'), s.get('secondary_source_id')):
                if sid:
                    self.by_source.setdefault(sid, s['name'])
                    parts = sid.split('-')
                    if len(parts) > 1:
                        self.by_token.setdefault(parts[1].upper(), s['name'])
        self.companies = companies

    @staticmethod
    def _tokens(name: str) -> set[str]:
        stop = {
            'inc',
            'usa',
            'corp',
            'corporation',
            'company',
            'the',
            'and',
            'llc',
            'support',
            'brand',
            'plant',
            'electric',
            'technology',
            'controls',
            'automation',
            'water',
            'mfg',
            'valve',
            'safety',
            'pumps',
            'industrial',
        }
        return {
            t
            for t in re.split(r'[^a-z0-9+-]+', name.lower())
            if len(t) > 2 and t not in stop
        }

    def _company(self, name: str) -> str:
        wanted = self._tokens(name)
        for c in self.companies:
            if wanted & self._tokens(c):
                return c
        return f'NEW: {name}'

    def get(self, stem: str) -> str:
        """Company for a capture file stem, or '' when unknown."""
        name = self.by_source.get(stem)
        if not name:
            parts = stem.split('-')
            name = self.by_token.get(parts[1].upper()) if len(parts) > 1 else None
        if not name and len(stem.split('-')) > 1:
            return f'NEW: {stem.split("-")[1].title()}'
        return self._company(name) if name else ''


def build(package: Path, workbook: Path, out: Path) -> dict:
    """Write the plan files and return the summary."""
    book = openpyxl.load_workbook(workbook, read_only=True, data_only=True)
    targets = Targets(book)
    attachment_plan = sheet(book, 'Attachment Plan')
    selected = {
        str(r['document_hash']) for r in attachment_plan if r.get('document_hash')
    }

    catalogue = read_csv(package / '00_Start_Here' / 'SOURCE_CATALOG.csv')
    by_path: dict[str, dict] = {}
    roles_by_path: dict[str, set[str]] = collections.defaultdict(set)
    for r in catalogue:
        p = r['package_relative_path']
        if not p:
            continue
        by_path.setdefault(p, r)
        roles_by_path[p].update(roles_of(r, p))
    by_sha = {}
    for p, r in by_path.items():
        by_sha.setdefault(r['sha256'], p)

    eh_latest: dict[str, tuple] = {}
    for p, r in by_path.items():
        for name in (r.get('title', ''), Path(p).name):
            eh = eh_key(name)
            if eh and eh[1] == 'EN':
                eh_latest[eh[0]] = max(eh_latest.get(eh[0], eh[2]), eh[2])

    suppliers = read_csv(
        package / '04_Maintenance_and_Procurement' / 'supplier_reference_master.csv'
    )
    companies = [str(c['name']) for c in sheet(book, 'Companies')]
    supplier_by_source = Suppliers(suppliers, companies)

    plan: list[dict] = []
    for root, _dirs, files in os.walk(package):
        for name in sorted(files):
            if name == '.DS_Store':
                continue
            absolute = Path(root) / name
            rel = absolute.relative_to(package).as_posix()
            row = dict(by_path.get(rel, {}))
            roles = roles_by_path.get(rel) or set(roles_of(row, rel))
            plan.extend(
                classify(
                    rel,
                    absolute,
                    row,
                    roles,
                    targets,
                    eh_latest,
                    selected,
                    supplier_by_source,
                )
            )
    plan.sort(
        key=lambda r: (
            r['section'],
            r['target_key'],
            r['priority'],
            r['package_relative_path'],
        )
    )

    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / 'reference_upload_plan.csv', PLAN_FIELDS, plan)

    resolved = resolve_attachment_plan(attachment_plan, by_sha, plan, targets)
    write_csv(out / 'attachment_plan_resolved.csv', list(resolved[0].keys()), resolved)

    files = {r['package_relative_path'] for r in plan}
    uploads = [
        r
        for r in plan
        if r['decision'].startswith('UPLOAD') or r['decision'] == 'LINK_ONLY'
    ]
    summary = {
        'package': str(package),
        'workbook': str(workbook),
        'files_classified': len(files),
        'plan_rows': len(plan),
        'by_section': dict(collections.Counter(r['section'] for r in plan)),
        'by_decision': dict(collections.Counter(r['decision'] for r in plan)),
        'uploads_by_priority': dict(
            collections.Counter(r['priority'] for r in uploads)
        ),
        'uploads_by_target_type': dict(
            collections.Counter(r['target_model_type'] for r in uploads)
        ),
        'uploads_by_target': dict(
            sorted(collections.Counter(r['target_key'] for r in uploads).items())
        ),
        'attachment_plan': dict(collections.Counter(r['resolution'] for r in resolved)),
    }
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def classify(
    rel, absolute, row, roles, targets, eh_latest, selected, supplier_by_source
):
    """Plan rows for one file (one per library role for equipment files)."""
    section = rel.split('/')[0] if '/' in rel else '(root)'
    ext = absolute.suffix.lower()
    title = row.get('title') or absolute.name
    base = {
        'section': section,
        'package_relative_path': rel,
        'title': title,
        'extension': ext,
        'sha256': row.get('sha256', ''),
        'library_roles': ';'.join(sorted(roles)),
        'doc_kind': '',
        'decision': '',
        'priority': '',
        'target_model_type': '',
        'target_key': '',
        'target_ui': '',
        'also_applies_to': '',
        'rag_effect': 'none',
        'reason': '',
        'package_use_limit': row.get('indexing_disposition', ''),
        'source_url': row.get('source_url', ''),
        'absolute_path': str(absolute),
    }

    def target(row_, model_type, key, also=''):
        row_['target_model_type'] = model_type
        row_['target_key'] = key
        row_['also_applies_to'] = also
        row_['target_ui'] = UI_PATH.get(model_type, '').format(key=key)
        row_['rag_effect'] = rag_effect(row_['decision'], model_type, ext)
        return row_

    if section == '01_Equipment_Manuals_and_Drawings':
        if '/00_Browse_by_Equipment/' in rel or rel.endswith('/INDEX.html'):
            return [
                dict(
                    base,
                    doc_kind='package_index',
                    decision='NOT_AN_ATTACHMENT',
                    reason='Package navigation page',
                )
            ]
        try:
            archive = json.loads(row.get('historical_path_fields') or '{}').get(
                'archive_path', ''
            )
        except ValueError:
            archive = ''
        folder = archive.split('/')[-2] if archive.count('/') else ''
        kind = KIND_BY_FOLDER.get(folder) or (
            'drawing'
            if ext in CAD_EXT
            else 'image'
            if ext in IMAGE_EXT
            else 'web_capture'
            if ext in LINK_EXT
            else 'other_technical'
        )
        decision, reason = decide_equipment(
            dict(
                row, title=row.get('title') or absolute.name, _file_name=absolute.name
            ),
            ext,
            kind,
            eh_latest,
        )
        if not row:
            reason += ' (not in SOURCE_CATALOG)'
        if '/targeted_page_review/' in rel:
            decision, reason = INTERNAL_REVIEW
        rows = []
        for role in sorted(roles) or ['']:
            r = dict(
                base,
                doc_kind=kind,
                decision=decision,
                reason=reason,
                priority=priority(kind, base['sha256'], selected),
            )
            if role:
                model_type, key, also = targets.for_role(role)
                target(r, model_type, key, also)
            else:
                r['reason'] += '; no library role recorded: place by hand'
            rows.append(r)
        return rows

    if section == '04_Maintenance_and_Procurement':
        stem = absolute.stem
        if '/originals/' in rel or '/ancillary_sources/' in rel:
            if stem.startswith(MAINTENANCE_PREFIXES):
                r = dict(
                    base,
                    doc_kind='maintenance_guidance',
                    priority='P2',
                    decision='UPLOAD' if ext in DOC_EXT else 'LINK_ONLY',
                    reason='Station-wide maintenance/calibration guidance for PM plans',
                )
                return [target(r, 'assetmachine', 'CC-STATION')]
            company = supplier_by_source.get(stem)
            if stem.startswith(('PROC-', 'ANC-', 'SUPP-')):
                r = dict(
                    base,
                    doc_kind='supplier_capture',
                    priority='P3',
                    decision='LINK_ONLY' if ext in LINK_EXT else 'UPLOAD_FILE_ONLY',
                    reason='Dated supplier/contact capture; fields go into Company/Contact/Address',
                )
                if company:
                    return [target(r, 'company', company)]
                r['reason'] += '; supplier not in supplier_reference_master'
                return [r]
        return [
            dict(
                base,
                doc_kind='register',
                decision='DATA_SOURCE',
                priority='P2',
                target_model_type=data_target(rel),
                reason=data_reason(rel),
            )
        ]

    if section == '06_Configuration_and_Applicability':
        if absolute.name in ER_IMAGE_ROLES:
            role = ER_IMAGE_ROLES[absolute.name]
            image = ext in IMAGE_EXT
            decision, reason = (
                INTERNAL_REVIEW
                if image
                else (
                    'UPLOAD',
                    'Engineering-review source for the role (reference, not approval)',
                )
            )
            r = dict(
                base,
                library_roles=role,
                priority='P2',
                doc_kind='drawing_excerpt' if image else 'manual',
                decision=decision,
                reason=reason,
            )
            model_type, key, also = targets.for_role(role)
            return [target(r, model_type, key, also)]
        return [
            dict(
                base,
                doc_kind='engineering_register',
                decision='DATA_SOURCE',
                priority='P3',
                target_model_type=data_target(rel),
                reason=data_reason(rel),
            )
        ]

    fixed = {
        '00_Start_Here': (
            'NOT_AN_ATTACHMENT',
            'Package index/integrity record; keep with the package',
        ),
        '02_Compliance_and_Reporting': (
            'HOLD_COMPLIANCE',
            'No compliance model yet (Import Sequence step 16). Regulatory text only as a '
            'ControlledDocument (Markdown, legal review) - never as a machine attachment',
        ),
        '03_External_Measured_Data': (
            'DO_NOT_UPLOAD',
            'Public third-party datasets (KAIST/OscGrid/UCI/BSM): not Cedar Creek data; '
            'analytics test fixtures only, with their own licences',
        ),
        '05_InvenTree_Reference_Schemas': (
            'DEV_REFERENCE',
            'Upstream API/schema reference for developers',
        ),
        '90_Provenance_and_Historical_Reviews': (
            'DO_NOT_UPLOAD',
            'Historical audit trail; keep unchanged',
        ),
        '(root)': ('NOT_AN_ATTACHMENT', 'Package start page'),
    }
    decision, reason = fixed.get(section, ('REVIEW', 'Unknown section'))
    return [dict(base, decision=decision, reason=reason)]


def data_target(rel: str) -> str:
    """InvenTree record type a register feeds."""
    name = Path(rel).name
    table = {
        'supplier_reference_master': 'company+contact+address',
        'supplier_offers': 'supplierpart+pricebreak',
        'supplier_offer_observation': 'supplierpart+pricebreak',
        'supplier_role_coverage_all50': 'company (per-role supplier route)',
        'maintenance_functional_coverage': 'pm_plan references',
        'part_facts': 'manufacturerpart (candidate)',
        'repair_parts_reference_callouts': 'part notes/parameters',
        'reference_variant_decisions': 'machine profile/part parameters (TARGET)',
        'actuation_stored_energy_inputs': 'repair safety gate inputs',
        'AIMMS_acceptance_cases': 'AI evaluation fixtures',
        'parent_catalog_part_candidates': 'part candidates (unapproved)',
    }
    for key, value in table.items():
        if name.startswith(key):
            return value
    return ''


def data_reason(rel: str) -> str:
    """Why a register is data and not an attachment."""
    target = data_target(rel)
    if target:
        return f'Structured register: feeds {target}; import as records, do not attach'
    return 'Evidence/provenance metadata; keep with the package'


def resolve_attachment_plan(rows, by_sha, plan, targets):
    """Resolve the workbook Attachment Plan to new package paths."""
    plan_by_path = collections.defaultdict(list)
    for p in plan:
        plan_by_path[p['package_relative_path']].append(p)
    best_by_role: dict[str, dict] = {}
    rank = {
        'manual': 0,
        'parts_list': 1,
        'datasheet': 2,
        'drawing': 3,
        'other_technical': 4,
    }
    for p in plan:
        if (
            p['decision'] != 'UPLOAD'
            or not p['library_roles']
            or p['extension'] != '.pdf'
        ):
            continue
        for role in p['library_roles'].split(';'):
            current = best_by_role.get(role)
            key = (title_score(p['title']), rank.get(p['doc_kind'], 9), p['title'])
            if current is None or key < (
                title_score(current['title']),
                rank.get(current['doc_kind'], 9),
                current['title'],
            ):
                best_by_role[role] = p
    resolved = []
    for r in rows:
        role_m = re.search(r'LIB-P\d\d', str(r.get('target') or ''))
        role = role_m[0] if role_m else ''
        sha = str(r.get('document_hash') or '')
        path = by_sha.get(sha, '')
        entry = plan_by_path.get(path, [{}])[0] if path else {}
        if r.get('target') == 'PUMP_TRAIN':
            resolution = 'NOT_IN_PACKAGE_SITE_EVIDENCE_NEEDED'
            note = 'Site photo/diagram: must come from the plant, never generated'
            model_type, key = 'assetmachine', str(r.get('target_machine_key'))
        elif not sha:
            resolution = 'MISSING_IN_PACKAGE'
            note = (
                'Targeted acquisition still needed (see MISSING_EXTERNAL_INFORMATION)'
            )
            model_type, key, _ = targets.for_role(role)
        elif not path:
            resolution = 'HASH_NOT_FOUND'
            note = 'Workbook hash not present in the package'
            model_type, key, _ = targets.for_role(role)
        else:
            weak = 'ISO 9001' in str(r.get('document_title')) or (
                entry.get('decision', '').startswith('SKIP')
            )
            resolution = 'RESOLVED_WEAK_CHOICE' if weak else 'RESOLVED'
            note = entry.get('reason', '')
            model_type, key, _ = targets.for_role(role)
        better = best_by_role.get(role)
        needs_alternative = r.get('target') != 'PUMP_TRAIN' and (
            resolution != 'RESOLVED'
            or not entry.get('decision', '').startswith('UPLOAD')
        )
        suggest = (
            better
            if better and needs_alternative and better['package_relative_path'] != path
            else None
        )
        resolved.append({
            'attachment_key': r.get('attachment_key'),
            'library_role': role,
            'display_title': r.get('display_title'),
            'workbook_document_title': r.get('document_title'),
            'resolution': resolution,
            'package_relative_path': path,
            'decision': entry.get('decision', ''),
            'target_model_type': model_type,
            'target_key': key,
            'component': r.get('target_component_code') or '',
            'suggested_alternative': suggest['package_relative_path']
            if suggest
            else '',
            'suggested_alternative_title': suggest['title'] if suggest else '',
            'note': note,
        })
    return resolved


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    """Write rows as CSV."""
    with path.open('w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--package', required=True, type=Path)
    parser.add_argument('--workbook', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    summary = build(args.package.resolve(), args.workbook.resolve(), args.out)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
