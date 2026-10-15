"""Bind reference-library documents to parts or machines as link-only attachments.

Inputs are the population workbook's ``Attachment Plan`` sheet and the
consolidated external-reference pack (``AIMMS Demo Data Final``). Only
``REFERENCE_LIBRARY_LIB-Pnn`` rows are handled; each becomes one link-only
``common.Attachment`` on the record a client user can reach for that role:

* the family part ``<prefix>Pnn`` (default ``CC-REF-``) when it is linked to
  a machine through ``MachinePart`` (pump-train roles reach every pump);
* otherwise the support machine whose serial is ``<prefix>Pnn-01``, because
  an unlinked part attachment falls back to the ``internal`` client scope;
* otherwise the family part.

No file bytes are copied: the pack's rights review says public
retrieval is not a licence to reproduce OEM documents, and link-only
attachments are never fetched or indexed by the attachment-RAG pipeline.

A row is held, not linked, when any of these is true:

* the workbook names no document (``MISSING_EXACT_SELECTED_MANUAL``);
* the document hash is not in the pack's ``FILE_MANIFEST.csv``;
* any pack catalogue record for that hash has a ``HOLD_*`` / ``EXCLUDE_*``
  indexing disposition or a ``FAILED`` validation status;
* the reviewed hold list (``--review`` JSON) names the attachment key.

Pump-train rows (nameplate photos, GA excerpts, lockout diagrams) have no
file in the pack and are reported as manual work.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

SHEET = 'Attachment Plan'
COLUMNS = (
    'attachment_key',
    'target',
    'display_title',
    'document_title',
    'document_hash',
    'source_file',
    'source_url',
    'approval_status',
    'acquisition_status',
)
TARGET_PREFIX = 'REFERENCE_LIBRARY_'
ROLE = re.compile(r'^LIB-P(\d{2})$')
SHA256 = re.compile(r'^[0-9a-f]{64}$')
HOLD_DISPOSITION_PREFIXES = ('HOLD_', 'EXCLUDE_')
FAILED_VALIDATION = 'FAILED'
METADATA_KEY = 'aimms_reference_pack'
COMMENT_MAX = 250
MANIFEST = '00_Start_Here/FILE_MANIFEST.csv'
CATALOG = '00_Start_Here/SOURCE_CATALOG.csv'


class ReferenceLibraryError(Exception):
    """Input that would make a binding ambiguous."""


@dataclass(frozen=True)
class PlanRow:
    """One reference-library row and what will happen to it."""

    key: str
    role: str
    ipn: str
    title: str
    url: str
    sha256: str
    package_path: str
    action: str  # create | unchanged | held | blocked | conflict
    reason: str = ''
    target_type: str = ''  # part | assetmachine
    target_pk: int | None = None
    target_label: str = ''
    approval_status: str = ''


@dataclass
class ReferencePlan:
    """Plan for every reference-library row plus counts of manual rows."""

    workbook_sha256: str
    rows: list[PlanRow] = field(default_factory=list)
    manual: dict[str, int] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        """Rows per action."""
        return dict(Counter(row.action for row in self.rows))

    def as_dict(self) -> dict:
        """JSON-serialisable view."""
        return {
            'workbook_sha256': self.workbook_sha256,
            'counts': self.counts(),
            'manual': self.manual,
            'rows': [asdict(row) for row in self.rows],
        }


# -- inputs --------------------------------------------------------------------


def _text(value) -> str:
    if value is None:
        return ''
    return str(value).strip()


def load_attachment_plan(path) -> tuple[str, list[dict], dict[str, int]]:
    """Return (sha256, reference rows, manual counts) from the workbook."""
    from openpyxl import load_workbook

    file = Path(path)
    if not file.is_file():
        raise ReferenceLibraryError(f'Workbook not found: {file}')
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    try:
        book = load_workbook(file, read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises several unrelated types
        raise ReferenceLibraryError(f'Cannot open workbook: {exc}') from exc
    try:
        if SHEET not in book.sheetnames:
            raise ReferenceLibraryError(f'Missing sheet: {SHEET}')
        iterator = book[SHEET].iter_rows(values_only=True)
        try:
            header = [_text(cell) for cell in next(iterator)]
        except StopIteration as exc:
            raise ReferenceLibraryError(f'{SHEET}: sheet is empty') from exc
        missing = [c for c in COLUMNS if c not in header]
        if missing:
            raise ReferenceLibraryError(
                f'{SHEET}: missing columns {", ".join(missing)}'
            )
        index = {c: header.index(c) for c in COLUMNS}
        rows: list[dict] = []
        manual: Counter = Counter()
        seen: set[str] = set()
        for values in iterator:
            if not values or all(v is None or _text(v) == '' for v in values):
                continue
            padded = list(values) + [None] * (len(header) - len(values))
            cell = {c: _text(padded[i]) for c, i in index.items()}
            key = cell['attachment_key']
            if not key:
                raise ReferenceLibraryError(f'{SHEET}: a row has no attachment_key')
            if key in seen:
                raise ReferenceLibraryError(f'{SHEET}: duplicate attachment_key {key}')
            seen.add(key)
            if cell['target'].startswith(TARGET_PREFIX):
                rows.append(cell)
            else:
                manual[cell['target'] or 'UNSPECIFIED'] += 1
    finally:
        book.close()
    return digest, rows, dict(manual)


def load_pack_index(pack_dir) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """Return (manifest by sha256, catalogue records by sha256)."""
    root = Path(pack_dir)
    manifest_file = root / MANIFEST
    catalog_file = root / CATALOG
    for file in (manifest_file, catalog_file):
        if not file.is_file():
            raise ReferenceLibraryError(f'Pack index not found: {file}')
    csv.field_size_limit(sys.maxsize)
    manifest: dict[str, dict] = {}
    with manifest_file.open(newline='', encoding='utf-8') as handle:
        for record in csv.DictReader(handle):
            manifest.setdefault(record['sha256'], record)
    catalog: dict[str, list[dict]] = {}
    with catalog_file.open(newline='', encoding='utf-8') as handle:
        for record in csv.DictReader(handle):
            catalog.setdefault(record.get('sha256', ''), []).append({
                'indexing_disposition': record.get('indexing_disposition', ''),
                'validation_status': record.get('validation_status', ''),
            })
    return manifest, catalog


def load_review(path) -> dict[str, str]:
    """Reviewed hold list: ``{"holds": {attachment_key: reason}}``."""
    if not path:
        return {}
    file = Path(path)
    if not file.is_file():
        raise ReferenceLibraryError(f'Review file not found: {file}')
    try:
        data = json.loads(file.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise ReferenceLibraryError(f'Review file is not JSON: {exc}') from exc
    holds = data.get('holds', {})
    if not isinstance(holds, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and v.strip()
        for k, v in holds.items()
    ):
        raise ReferenceLibraryError(
            'Review "holds" must map attachment_key to a reason'
        )
    return holds


# -- planning ------------------------------------------------------------------


def _pack_hold_reason(records: list[dict]) -> str:
    for record in records:
        disposition = record['indexing_disposition']
        if disposition.startswith(HOLD_DISPOSITION_PREFIXES):
            return f'pack disposition {disposition}'
    for record in records:
        if record['validation_status'] == FAILED_VALIDATION:
            return 'pack validation FAILED'
    return ''


def _existing_attachment(model_type: str, pk: int, key: str, url: str):
    from django.db.models import Q

    from common.models import Attachment

    return (
        Attachment.objects
        .filter(model_type=model_type, model_id=pk)
        .filter(Q(**{f'metadata__{METADATA_KEY}__attachment_key': key}) | Q(link=url))
        .first()
    )


def resolve_target(ipn: str) -> tuple[str, int | None, str, str]:
    """(model_type, pk, label, problem) for a family IPN such as ``CC-REF-P11``.

    ``problem`` is empty on success, else the reason the row cannot bind.
    """
    from assets.models import AssetMachine, MachinePart
    from part.models import Part

    parts = list(Part.objects.filter(IPN__iexact=ipn).values_list('pk', flat=True)[:2])
    if len(parts) > 1:
        return '', None, ipn, f'more than one part has IPN {ipn}'
    part_pk = parts[0] if parts else None
    if part_pk and MachinePart.objects.filter(part_id=part_pk).exists():
        return 'part', part_pk, ipn, ''
    serial = f'{ipn}-01'
    machines = list(
        AssetMachine.objects.filter(serial__iexact=serial).values_list('pk', 'name')[:2]
    )
    if len(machines) > 1:
        return '', None, serial, f'more than one machine has serial {serial}'
    if machines:
        return 'assetmachine', machines[0][0], f'{machines[0][1]} ({serial})', ''
    if part_pk:
        return 'part', part_pk, ipn, ''
    return '', None, ipn, f'neither part {ipn} nor a machine with serial {serial} found'


def plan_reference_links(
    rows: list[dict],
    manifest: dict[str, dict],
    catalog: dict[str, list[dict]],
    holds: dict[str, str],
    *,
    pack_dir,
    ipn_prefix: str = 'CC-REF-',
    workbook_sha256: str = '',
    manual: dict[str, int] | None = None,
) -> ReferencePlan:
    """Decide an action for each reference-library row. Reads, never writes."""
    plan = ReferencePlan(workbook_sha256=workbook_sha256, manual=dict(manual or {}))
    root = Path(pack_dir)
    for cell in rows:
        key = cell['attachment_key']
        role = cell['target'][len(TARGET_PREFIX) :]
        match = ROLE.match(role)
        if not match:
            raise ReferenceLibraryError(f'{key}: unrecognised target {cell["target"]}')
        ipn = f'{ipn_prefix}P{match.group(1)}'
        title = cell['document_title'] or cell['display_title']
        url = cell['source_url']
        sha = cell['document_hash'].lower()
        common = {
            'key': key,
            'role': role,
            'ipn': ipn,
            'title': title,
            'url': url,
            'sha256': sha,
            'approval_status': cell['approval_status'],
        }

        def row(action, reason='', path='', target=('', None, ''), _c=common):
            return PlanRow(
                **_c,
                action=action,
                reason=reason,
                package_path=path,
                target_type=target[0],
                target_pk=target[1],
                target_label=target[2],
            )

        if not sha:
            plan.rows.append(row('held', f'no document named ({title})'))
            continue
        if not SHA256.match(sha):
            raise ReferenceLibraryError(f'{key}: document_hash is not a sha256')
        entry = manifest.get(sha)
        if entry is None:
            plan.rows.append(row('held', 'document hash not in pack manifest'))
            continue
        path = entry['package_relative_path']
        file = root / path
        if not file.is_file():
            plan.rows.append(row('held', 'pack file missing on disk', path))
            continue
        if str(file.stat().st_size) != str(entry.get('bytes', '')):
            plan.rows.append(row('held', 'pack file size differs from manifest', path))
            continue
        reason = _pack_hold_reason(catalog.get(sha, []))
        if reason:
            plan.rows.append(row('held', reason, path))
            continue
        if key in holds:
            plan.rows.append(row('held', f'reviewed hold: {holds[key]}', path))
            continue
        if urlparse(url).scheme not in ('http', 'https'):
            plan.rows.append(row('held', 'no http(s) source URL to link', path))
            continue
        model_type, pk, label, problem = resolve_target(ipn)
        if problem:
            action = 'conflict' if problem.startswith('more than one') else 'blocked'
            plan.rows.append(row(action, problem, path))
            continue
        target = (model_type, pk, label)
        existing = _existing_attachment(model_type, pk, key, url)
        if existing is not None:
            plan.rows.append(
                row('unchanged', f'attachment {existing.pk}', path, target)
            )
            continue
        plan.rows.append(row('create', '', path, target))
    return plan


# -- apply ---------------------------------------------------------------------


def _comment(row: PlanRow) -> str:
    text = (
        f'{row.title} [{row.key}, {row.role}] reference only; '
        'not approved for the installed build'
    )
    return text if len(text) <= COMMENT_MAX else text[: COMMENT_MAX - 1] + '…'


def apply_reference_links(plan: ReferencePlan, actor=None, *, dry_run: bool) -> dict:
    """Create the planned link-only attachments in one transaction."""
    from django.db import transaction

    from common.models import Attachment

    created: list[dict] = []
    with transaction.atomic():
        for row in plan.rows:
            if row.action != 'create':
                continue
            attachment = Attachment(
                model_type=row.target_type,
                model_id=row.target_pk,
                link=row.url,
                comment=_comment(row),
                upload_user=actor,
                metadata={
                    METADATA_KEY: {
                        'attachment_key': row.key,
                        'library_role': row.role,
                        'sha256': row.sha256,
                        'package_relative_path': row.package_path,
                        'approval_status': row.approval_status,
                        'workbook_sha256': plan.workbook_sha256,
                    }
                },
            )
            attachment.full_clean(exclude=['attachment', 'thumbnail'])
            attachment.save(rebuild=False)
            created.append({
                'key': row.key,
                'target': f'{row.target_type}:{row.target_label}',
                'attachment': attachment.pk,
            })
        if dry_run:
            transaction.set_rollback(True)
    return {'dry_run': dry_run, 'created': created, 'counts': plan.counts()}
