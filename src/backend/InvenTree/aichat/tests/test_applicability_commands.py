"""S8b WP-C8: the applicability workflow commands."""

import json
import uuid
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from assets.models import AssetMachine, Client
from aichat.models import (
    ApplicabilityState,
    ControlledDocument,
    ControlledDocumentApplicability,
    ControlledDocumentState,
)

SCOPE_KEY = 'epcon-experimental'


def _permission(codename: str) -> Permission:
    return Permission.objects.get(codename=codename, content_type__app_label='aichat')


def _call(name: str, *args) -> str:
    out = StringIO()
    call_command(name, *args, stdout=out)
    return out.getvalue()


class ApplicabilityCommandTestCase(TestCase):
    """One document, two machines, humans with distinct authority."""

    @classmethod
    def setUpTestData(cls):
        suffix = uuid.uuid4().hex[:6]
        cls.suffix = suffix
        users = get_user_model().objects
        cls.proposer = users.create_user(username=f'cmd-proposer-{suffix}')
        cls.verifier = users.create_user(username=f'cmd-verifier-{suffix}')
        cls.engineer = users.create_user(username=f'cmd-engineer-{suffix}')
        cls.outsider = users.create_user(username=f'cmd-outsider-{suffix}')
        cls.verifier.user_permissions.add(_permission('verify_document_applicability'))
        cls.engineer.user_permissions.add(
            _permission('countersign_document_applicability')
        )
        cls.tenant = Client.objects.create(
            name=f'Cmd Plant {suffix}', code=f'cmd-{suffix}'
        )
        cls.machine = AssetMachine.objects.create(
            name=f'Cmd HX-200 {suffix}', client=cls.tenant, serial=f'CMD-HX200-{suffix}'
        )
        cls.document = ControlledDocument.objects.create(
            document_id=f'cmd-manual-{suffix}',
            revision='2.0',
            title='HX-200 Technical Manual',
            document_class='technical_manual',
            scope_key=SCOPE_KEY,
            scope_hash='a' * 64,
            access_class='maintenance_authorized',
            source_filename='hx200-manual.md',
            source_location='/tmp/hx200-manual.md',
            source_sha256='b' * 64,
            asset_id=f'CMD-HX200-{suffix}',
            state=ControlledDocumentState.INDEXED,
            is_current=True,
            search_index_name='eaits-manuals-v4a',
        )

    def _document_args(self):
        return (
            '--scope-key', SCOPE_KEY,
            '--document-id', self.document.document_id,
            '--revision', '2.0',
        )

    def _propose(self, *extra) -> int:
        output = _call(
            'applicability_propose',
            *self._document_args(),
            '--kind', 'exact_machine',
            '--machine-id', str(self.machine.pk),
            '--serial', self.machine.serial,
            '--basis', 'commissioning record',
            '--by', self.proposer.username,
            '--json',
            *extra,
        )
        return json.loads(output)['claim']


class WorkflowCommandTests(ApplicabilityCommandTestCase):
    """The verbs drive the service; failure is a hard CommandError."""

    def test_propose_then_verify_activates(self):
        claim = self._propose()
        row = ControlledDocumentApplicability.objects.get(pk=claim)
        self.assertEqual(row.state, ApplicabilityState.PROPOSED)
        output = _call(
            'applicability_verify', '--claim', str(claim), '--by', self.verifier.username
        )
        self.assertIn('VERIFIED', output)

    def test_missing_permission_is_a_command_error(self):
        claim = self._propose()
        with self.assertRaises(CommandError):
            call_command(
                'applicability_verify',
                '--claim', str(claim),
                '--by', self.outsider.username,
            )
        with self.assertRaises(CommandError):
            call_command(
                'applicability_verify', '--claim', str(claim), '--by', 'nobody-here'
            )

    def test_model_kind_needs_both_signatures(self):
        output = _call(
            'applicability_propose',
            *self._document_args(),
            '--kind', 'inverter_model',
            '--model', 'SINVERT PVS351',
            '--basis', 'nameplate survey',
            '--by', self.proposer.username,
            '--json',
        )
        claim = json.loads(output)['claim']
        verify_out = _call(
            'applicability_verify', '--claim', str(claim), '--by', self.verifier.username
        )
        self.assertIn('countersign', verify_out)
        sign_out = _call(
            'applicability_countersign',
            '--claim', str(claim),
            '--by', self.engineer.username,
        )
        self.assertIn('VERIFIED', sign_out)

    def test_revoke_records_the_reason(self):
        claim = self._propose()
        _call('applicability_verify', '--claim', str(claim), '--by', self.verifier.username)
        _call(
            'applicability_revoke',
            '--claim', str(claim),
            '--reason', 'wrong unit',
            '--by', self.verifier.username,
        )
        row = ControlledDocumentApplicability.objects.get(pk=claim)
        self.assertEqual(row.state, ApplicabilityState.REVOKED)
        self.assertEqual(row.revoke_reason, 'wrong unit')


class ReportCommandTests(ApplicabilityCommandTestCase):
    """The report names the queue and the byte-stale rows."""

    def test_report_shows_pending_and_stale(self):
        claim = self._propose()
        report = json.loads(_call('applicability_report', '--json'))
        self.assertEqual(report['by_state'].get('proposed'), 1)
        self.assertEqual(report['pending'][0]['claim'], claim)
        _call('applicability_verify', '--claim', str(claim), '--by', self.verifier.username)
        self.document.source_sha256 = 'c' * 64
        self.document.save(update_fields=['source_sha256'])
        report = json.loads(_call('applicability_report', '--json'))
        self.assertEqual([entry['claim'] for entry in report['stale_hash']], [claim])


class BackfillCommandTests(ApplicabilityCommandTestCase):
    """Only an exactly-one serial match may become a proposed row."""

    def test_dry_run_writes_nothing(self):
        report = json.loads(
            _call('applicability_backfill', '--by', self.proposer.username, '--json')
        )
        self.assertEqual(report['mode'], 'dry_run')
        self.assertEqual(len(report['proposed']), 1)
        self.assertEqual(ControlledDocumentApplicability.objects.count(), 0)

    def test_yes_creates_proposed_rows_only(self):
        report = json.loads(
            _call(
                'applicability_backfill',
                '--by', self.proposer.username,
                '--yes',
                '--json',
            )
        )
        self.assertEqual(len(report['proposed']), 1)
        row = ControlledDocumentApplicability.objects.get(
            pk=report['proposed'][0]['claim']
        )
        self.assertEqual(row.state, ApplicabilityState.PROPOSED)
        self.assertEqual(row.target_machine_id, self.machine.pk)
        # Idempotent: the second run reports it as already claimed.
        rerun = json.loads(
            _call(
                'applicability_backfill',
                '--by', self.proposer.username,
                '--yes',
                '--json',
            )
        )
        self.assertEqual(len(rerun['proposed']), 0)
        self.assertEqual(len(rerun['already_claimed']), 1)

    def test_ambiguous_serial_stays_unresolved(self):
        AssetMachine.objects.create(
            name=f'Cmd Twin {self.suffix}',
            client=self.tenant,
            serial=self.machine.serial,
        )
        report = json.loads(
            _call(
                'applicability_backfill',
                '--by', self.proposer.username,
                '--yes',
                '--json',
            )
        )
        self.assertEqual(len(report['proposed']), 0)
        self.assertEqual(len(report['ambiguous']), 1)
        self.assertEqual(ControlledDocumentApplicability.objects.count(), 0)
