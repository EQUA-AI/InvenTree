"""Reference-library links are gated by the pack's holds and are repeatable."""

import csv
import hashlib
import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from openpyxl import Workbook

from assets.models import AssetMachine, Client, MachinePart
from assets.reference_library import (
    METADATA_KEY,
    ReferenceLibraryError,
    apply_reference_links,
    load_attachment_plan,
    load_pack_index,
    load_review,
    plan_reference_links,
)
from common.models import Attachment
from part.models import Part

HEADER = [
    'attachment_key',
    'target',
    'target_machine_key',
    'display_title',
    'document_title',
    'document_hash',
    'source_file',
    'source_url',
    'approval_status',
    'acquisition_status',
]


class ReferenceLibraryTests(TestCase):
    """Plan, hold rules, apply and replay."""

    def setUp(self):
        """A pack with five files and a workbook with eight rows."""
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pack = self.root / 'pack'
        (self.pack / '00_Start_Here').mkdir(parents=True)
        self.files = {}
        manifest = []
        catalog = []
        specs = {
            'ok': ('REFERENCE_ONLY_APPLICABILITY_AND_RIGHTS_REVIEW_REQUIRED', 'STRUCTURE_OK'),
            'rights': ('HOLD_PROTECTED_CONTENT_FOR_COMPANY_USE_RIGHTS_REVIEW', 'STRUCTURE_OK'),
            'failed': ('REFERENCE_ONLY_APPLICABILITY_AND_RIGHTS_REVIEW_REQUIRED', 'FAILED'),
            'reviewed': ('REFERENCE_ONLY_APPLICABILITY_AND_RIGHTS_REVIEW_REQUIRED', 'STRUCTURE_OK'),
            'nopart': ('REFERENCE_ONLY_APPLICABILITY_AND_RIGHTS_REVIEW_REQUIRED', 'STRUCTURE_OK'),
            'support': ('REFERENCE_ONLY_APPLICABILITY_AND_RIGHTS_REVIEW_REQUIRED', 'STRUCTURE_OK'),
        }
        for name, (disposition, validation) in specs.items():
            body = f'%PDF-1.4 {name}'.encode()
            sha = hashlib.sha256(body).hexdigest()
            rel = f'01_Equipment/{name}.pdf'
            (self.pack / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.pack / rel).write_bytes(body)
            self.files[name] = sha
            manifest.append({'package_relative_path': rel, 'sha256': sha, 'bytes': len(body)})
            catalog.append({
                'sha256': sha,
                'indexing_disposition': disposition,
                'validation_status': validation,
            })
        self._csv('00_Start_Here/FILE_MANIFEST.csv', manifest)
        self._csv('00_Start_Here/SOURCE_CATALOG.csv', catalog)

        unknown = hashlib.sha256(b'not in pack').hexdigest()
        rows = [
            ('CC-DOC-01', 'P01', 'ok'),
            ('CC-DOC-02', 'P02', 'rights'),
            ('CC-DOC-03', 'P03', 'failed'),
            ('CC-DOC-04', 'P04', None),
            ('CC-DOC-05', 'P05', 'reviewed'),
            ('CC-DOC-06', 'P06', unknown),
            ('CC-DOC-07', 'P07', 'nopart'),
            ('CC-DOC-08', 'P08', 'support'),
        ]
        book = Workbook()
        ws = book.active
        ws.title = 'Attachment Plan'
        ws.append(HEADER)
        for key, role, doc in rows:
            sha = self.files.get(doc, doc) if doc else None
            ws.append([
                key,
                f'REFERENCE_LIBRARY_LIB-{role}',
                None,
                f'{role} reference',
                f'{role} manual' if doc else 'MISSING_EXACT_SELECTED_MANUAL',
                sha,
                'x.pdf' if doc else None,
                f'https://oem.example.com/{role}.pdf' if doc else None,
                'NOT_APPROVED_FOR_SELECTED_BUILD',
                'ARCHIVED_CANDIDATE',
            ])
        ws.append([
            'CC-ATT-PT01-01', 'PUMP_TRAIN', 'CC-PUMP-01', 'nameplate photo',
            None, None, None, None, 'PENDING_CREATION_OR_REVIEW', 'NOT_YET_ATTACHED',
        ])
        self.workbook = self.root / 'book.xlsx'
        book.save(self.workbook)
        self.review = self.root / 'review.json'
        self.review.write_text(json.dumps({'holds': {'CC-DOC-05': 'not the manual'}}))

        for role in ('P01', 'P02', 'P03', 'P04', 'P05', 'P06'):
            Part.objects.create(
                name=f'Family {role}', description='ref', IPN=f'CC-REF-{role}', virtual=True
            )
        client = Client.objects.create(name='Ref Lib Plant', code='ref-lib-plant')
        self.pump = AssetMachine.objects.create(name='Ref Pump 01', client=client)
        MachinePart.objects.create(
            machine=self.pump, part=Part.objects.get(IPN='CC-REF-P01')
        )
        self.support = AssetMachine.objects.create(
            name='Hoist 01', client=client, serial='CC-REF-P08-01'
        )

    def tearDown(self):
        """Remove stored attachment files and the temporary pack."""
        for attachment in Attachment.objects.all():
            attachment.delete()
        self.tmp.cleanup()

    def _csv(self, rel, records):
        with (self.pack / rel).open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
            writer.writeheader()
            writer.writerows(records)

    def _plan(self, with_files=False):
        sha, rows, manual = load_attachment_plan(self.workbook)
        manifest, catalog = load_pack_index(self.pack)
        return plan_reference_links(
            rows,
            manifest,
            catalog,
            load_review(self.review),
            pack_dir=self.pack,
            workbook_sha256=sha,
            manual=manual,
            with_files=with_files,
        )

    def test_plan_applies_every_hold_rule(self):
        """Clean rows bind to the linked part or the support machine; the rest hold."""
        plan = self._plan()
        actions = {row.key: (row.action, row.reason) for row in plan.rows}
        targets = {row.key: (row.target_type, row.target_pk) for row in plan.rows}
        part = Part.objects.get(IPN='CC-REF-P01')
        self.assertEqual(actions['CC-DOC-01'][0], 'create')
        self.assertEqual(targets['CC-DOC-01'], ('part', part.pk))
        self.assertEqual(actions['CC-DOC-08'][0], 'create')
        self.assertEqual(targets['CC-DOC-08'], ('assetmachine', self.support.pk))
        self.assertIn('HOLD_PROTECTED', actions['CC-DOC-02'][1])
        self.assertEqual(actions['CC-DOC-03'][1], 'pack validation FAILED')
        self.assertIn('no document named', actions['CC-DOC-04'][1])
        self.assertEqual(actions['CC-DOC-05'][1], 'reviewed hold: not the manual')
        self.assertEqual(actions['CC-DOC-06'][1], 'document hash not in pack manifest')
        self.assertEqual(actions['CC-DOC-07'][0], 'blocked')
        self.assertIn('CC-REF-P07-01', actions['CC-DOC-07'][1])
        self.assertEqual(plan.manual, {'PUMP_TRAIN': 1})
        self.assertEqual(Attachment.objects.count(), 0)

    def test_unlinked_part_without_support_machine_falls_back_to_part(self):
        """A family part with no machine link and no support machine still binds."""
        MachinePart.objects.all().delete()
        again = {row.key: row for row in self._plan().rows}
        self.assertEqual(again['CC-DOC-01'].target_type, 'part')
        self.assertEqual(again['CC-DOC-01'].action, 'create')

    def test_apply_creates_link_only_and_replay_is_noop(self):
        """Link attachments with provenance; a second plan reports unchanged."""
        result = apply_reference_links(self._plan(), None, dry_run=False)
        self.assertEqual(len(result['created']), 2)
        part = Part.objects.get(IPN='CC-REF-P01')
        attachment = Attachment.objects.get(model_type='part')
        self.assertEqual(attachment.model_id, part.pk)
        self.assertFalse(attachment.attachment)
        self.assertEqual(attachment.link, 'https://oem.example.com/P01.pdf')
        self.assertIn('not approved for the installed build', attachment.comment)
        meta = attachment.metadata[METADATA_KEY]
        self.assertEqual(meta['sha256'], self.files['ok'])
        self.assertEqual(meta['package_relative_path'], '01_Equipment/ok.pdf')
        machine_link = Attachment.objects.get(model_type='assetmachine')
        self.assertEqual(machine_link.model_id, self.support.pk)

        replay = self._plan()
        self.assertEqual(replay.counts().get('create', 0), 0)
        self.assertEqual(replay.counts()['unchanged'], 2)

    def test_dry_run_rolls_back(self):
        """Dry run reports the creates but leaves nothing behind."""
        result = apply_reference_links(self._plan(), None, dry_run=True)
        self.assertEqual(len(result['created']), 2)
        self.assertEqual(Attachment.objects.count(), 0)

    def test_size_mismatch_is_held(self):
        """A pack file that differs from its manifest size is not linked."""
        (self.pack / '01_Equipment/ok.pdf').write_bytes(b'changed')
        actions = {row.key: row.reason for row in self._plan().rows}
        self.assertEqual(actions['CC-DOC-01'], 'pack file size differs from manifest')

    def test_bad_review_file_rejected(self):
        """Holds must carry a reason."""
        self.review.write_text(json.dumps({'holds': {'CC-DOC-01': ''}}))
        with self.assertRaises(ReferenceLibraryError):
            load_review(self.review)

    def test_command_gates_apply(self):
        """--apply needs a backup reference; plan mode writes nothing."""
        args = [str(self.workbook), '--pack', str(self.pack), '--review', str(self.review)]
        with self.assertRaises(CommandError):
            call_command('attach_reference_library', *args, '--apply')
        out = StringIO()
        call_command('attach_reference_library', *args, stdout=out)
        self.assertIn('create=2', out.getvalue())
        self.assertIn('PUMP_TRAIN: 1 rows', out.getvalue())
        self.assertEqual(Attachment.objects.count(), 0)
        call_command(
            'attach_reference_library', *args, '--apply', '--backup-ref', 'test', stdout=StringIO()
        )
        self.assertEqual(Attachment.objects.count(), 2)

    def test_with_files_stores_verified_bytes(self):
        """File mode stores the pack bytes on the attachment and keeps the link."""
        result = apply_reference_links(self._plan(with_files=True), None, dry_run=False)
        self.assertTrue(all(entry['file'] for entry in result['created']))
        attachment = Attachment.objects.get(model_type='part')
        self.assertTrue(attachment.attachment)
        self.assertEqual(attachment.link, 'https://oem.example.com/P01.pdf')
        with attachment.attachment.open('rb') as handle:
            self.assertEqual(handle.read(), b'%PDF-1.4 ok')
        replay = self._plan(with_files=True)
        self.assertEqual(replay.counts()['unchanged'], 2)

    def test_with_files_adds_file_to_existing_link(self):
        """A link created earlier gains its file instead of a duplicate row."""
        apply_reference_links(self._plan(), None, dry_run=False)
        plan = self._plan(with_files=True)
        self.assertEqual(plan.counts()['add_file'], 2)
        apply_reference_links(plan, None, dry_run=False)
        self.assertEqual(Attachment.objects.count(), 2)
        self.assertTrue(all(a.attachment for a in Attachment.objects.all()))

    def test_file_over_upload_limit_stays_link_only(self):
        """Files above the upload limit are linked with a note, not stored."""
        with patch('assets.reference_library.upload_limit_bytes', return_value=5):
            plan = self._plan(with_files=True)
        row = next(r for r in plan.rows if r.key == 'CC-DOC-01')
        self.assertEqual(row.action, 'create')
        self.assertFalse(row.upload_file)
        self.assertIn('exceeds upload limit', row.file_note)

    def test_with_files_refused_on_remote_database_without_shared_media(self):
        """Remote file writes need an explicit shared-media confirmation."""
        args = [str(self.workbook), '--pack', str(self.pack), '--review', str(self.review)]
        module = 'assets.management.commands.attach_reference_library'
        with patch(f'{module}.is_local_database', return_value=False):
            with self.assertRaises(CommandError):
                call_command(
                    'attach_reference_library', *args, '--dry-run', '--with-files',
                    stdout=StringIO(),
                )
            call_command(
                'attach_reference_library', *args, '--dry-run', '--with-files',
                '--shared-media', stdout=StringIO(),
            )
        self.assertEqual(Attachment.objects.count(), 0)
