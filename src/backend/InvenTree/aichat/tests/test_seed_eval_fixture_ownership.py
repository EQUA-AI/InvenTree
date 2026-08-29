"""S6 (WP-A5): eval-fixture ownership — seeding, repair, audit.

The three seeders now place every evaluation entity under the dedicated
``eval-fixtures`` client, repair pre-S6 rows explicitly (get_or_create
defaults are create-only), link the gasket part so its documents follow the
machine, refuse production without ``--break-glass``, and print a
reversible ownership manifest. ``audit_eval_fixture_index`` is the merge
gate for the data operation.
"""

from io import StringIO
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from .test_attachment_rag_ingestion import RagFixtureTestCase, _ai_settings


def _seed_attachments(*args):
    out = StringIO()
    with (
        mock.patch('ai.core.config.get_settings', return_value=_ai_settings()),
        mock.patch('InvenTree.tasks.offload_task', return_value=True),
        mock.patch(
            'aichat.services.attachment_ingestion.run_ingest',
            return_value=SimpleNamespace(state='indexed'),
        ),
        mock.patch(
            'aichat.services.attachment_ingestion.restamp_machine_client_codes'
        ) as restamp,
    ):
        call_command('seed_attachment_eval_fixtures', *args, stdout=out)
    return out.getvalue(), restamp


@override_settings(AIMMS_ATTACHMENT_RAG_ENABLED=True)
class SeedOwnershipTests(RagFixtureTestCase):
    """Fresh seeds land on eval-fixtures; pre-S6 rows are repaired."""

    def test_production_is_refused_without_break_glass(self):
        """Tests run with DEBUG off — exactly the posture that must refuse."""
        with self.assertRaises(CommandError) as caught:
            _seed_attachments()
        self.assertIn('break-glass', str(caught.exception))

    def test_fresh_seed_creates_the_machine_under_eval_fixtures(self):
        """A first-time seed never touches the internal tenant."""
        from assets.models import AssetMachine, MachinePart

        _out, restamp = _seed_attachments('--break-glass')
        machine = AssetMachine.objects.get(name='RAG Eval HX-200 Heat Exchanger')
        self.assertEqual(machine.client.code, 'eval-fixtures')
        # Fresh creation is already correctly owned: no repair restamp runs.
        restamp.assert_not_called()
        # The gasket is INSTALLED on the machine so its documents derive the
        # eval client instead of the ['internal'] unlinked-part fallback.
        self.assertTrue(
            MachinePart.objects.filter(
                machine=machine, part__name='RAG Eval HX-200 Gasket Set'
            ).exists()
        )

    def test_pre_s6_machine_is_repointed_with_manifest_and_restamp(self):
        """The explicit repair branch: defaults are create-only."""
        from assets.models import AssetMachine, get_default_client

        AssetMachine.objects.create(
            name='RAG Eval HX-200 Heat Exchanger',
            client=get_default_client(),
            serial='EVAL-HX200',
        )
        out, restamp = _seed_attachments('--break-glass')
        machine = AssetMachine.objects.get(name='RAG Eval HX-200 Heat Exchanger')
        self.assertEqual(machine.client.code, 'eval-fixtures')
        restamp.assert_called_once_with(machine.pk)
        self.assertIn('ownership_changes', out)
        self.assertIn('"old": "internal"', out)
        self.assertIn('"new": "eval-fixtures"', out)

    def test_dry_run_writes_nothing(self):
        """Dry run reports the v2 fixture set and creates no clients."""
        from assets.models import Client

        out = StringIO()
        call_command('seed_attachment_eval_fixtures', '--dry-run', stdout=out)
        self.assertIn('aimms-attachment-fixtures-v2', out.getvalue())
        self.assertFalse(Client.objects.filter(code='eval-fixtures').exists())


class AuditEvalFixtureIndexTests(RagFixtureTestCase):
    """The zero-stale-copy audit is the data operation's merge gate."""

    def _eval_world(self):
        from assets.models import AssetMachine, Client

        eval_client = Client.objects.create(
            name='RAG Evaluation Fixtures', code='eval-fixtures'
        )
        return AssetMachine.objects.create(
            name='RAG Eval HX-200 Heat Exchanger',
            client=eval_client,
            serial='EVAL-HX200',
        )

    def _ingest_row(self, machine, codes):
        from aichat.models import (
            AttachmentIngest,
            AttachmentIngestPipeline,
            AttachmentIngestState,
        )

        return AttachmentIngest.objects.create(
            attachment_id=machine.pk * 1000,  # registry key only; no file needed
            model_type='assetmachine',
            model_id=machine.pk,
            client_codes=codes,
            source_sha256='0' * 64,
            pipeline=AttachmentIngestPipeline.DOC,
            state=AttachmentIngestState.INDEXED,
        )

    def test_stale_internal_stamp_fails_the_audit(self):
        """A row still carrying 'internal' is a nonzero exit."""
        machine = self._eval_world()
        self._ingest_row(machine, ['internal'])
        out = StringIO()
        with self.assertRaises(SystemExit):
            call_command('audit_eval_fixture_index', stdout=out)
        self.assertIn('STALE', out.getvalue())

    def test_clean_stamps_pass(self):
        """Rows carrying only eval-fixtures audit clean."""
        machine = self._eval_world()
        self._ingest_row(machine, ['eval-fixtures'])
        out = StringIO()
        call_command('audit_eval_fixture_index', stdout=out)
        self.assertIn('Zero stale copies', out.getvalue())

    def test_no_eval_machines_is_a_clean_noop(self):
        """Before the data operation there is nothing to audit."""
        out = StringIO()
        call_command('audit_eval_fixture_index', stdout=out)
        self.assertIn('nothing to audit', out.getvalue())

    def test_latch_on_failure_engages_the_pilot_stop(self):
        """S15/Q50(a): the opt-in eval-window trigger engages the latch."""
        from aichat.services.pilot_latch import current_state

        machine = self._eval_world()
        self._ingest_row(machine, ['internal'])
        out = StringIO()
        with self.assertRaises(SystemExit):
            call_command('audit_eval_fixture_index', '--latch-on-failure', stdout=out)
        self.assertIn('Pilot-stop latch ENGAGED', out.getvalue())
        state = current_state()
        self.assertTrue(state['latched'])
        self.assertEqual(state['reason_code'], 'eval_fixture_leak')

    def test_stale_stamp_without_the_flag_never_latches(self):
        """The default audit stays a pure gate — no side effects."""
        from aichat.services.pilot_latch import current_state

        machine = self._eval_world()
        self._ingest_row(machine, ['internal'])
        with self.assertRaises(SystemExit):
            call_command('audit_eval_fixture_index', stdout=StringIO())
        self.assertFalse(current_state()['latched'])
