"""R4 video eval fixture seeder: dry-run report, flag gate, fixture presence."""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from aichat.models import AttachmentIngest
from common.models import Attachment

from .test_attachment_rag_ingestion import (
    RagFixtureTestCase,
    _ai_settings,
    _media_ai_settings,
)


class SeedVideoEvalFixturesTests(RagFixtureTestCase):
    """The seeder is idempotent, full-flag gated, and loud about absence."""

    def test_dry_run_reports_without_writing(self):
        """Dry run names the fixture set and writes no entities at all."""
        from io import StringIO

        from tasks.models import WorkOrder

        out = StringIO()
        call_command('seed_video_eval_fixtures', '--dry-run', stdout=out)
        report = out.getvalue()
        self.assertIn('aimms-video-fixtures-v2', report)
        self.assertIn('owner absent (would create)', report)
        self.assertFalse(
            WorkOrder.objects.filter(reference='WO-EVAL-HX200-VIDEO').exists()
        )
        self.assertFalse(
            Attachment.objects.filter(comment__contains='RAG eval fixture').exists()
        )
        self.assertFalse(AttachmentIngest.objects.exists())

    @override_settings(
        AIMMS_ATTACHMENT_RAG_ENABLED=False, AIMMS_MEDIA_RAG_ENABLED=False
    )
    def test_live_run_requires_the_full_flag_conjunction(self):
        """A live run refuses while ANY of the three media planes is dark.

        R5 default-on: the dark Django pair must be EXPLICIT now, or the
        refusal message only names the (degraded) AI flag.
        """
        with (
            mock.patch('ai.core.config.get_settings', return_value=_ai_settings()),
            self.assertRaises(CommandError) as caught,
        ):
            call_command('seed_video_eval_fixtures')
        message = str(caught.exception)
        for flag in (
            'AIMMS_ATTACHMENT_RAG_ENABLED',
            'AIMMS_MEDIA_RAG_ENABLED',
            'FEATURE_MEDIA_RAG_INGEST',
        ):
            self.assertIn(flag, message)

    def test_missing_fixture_recording_fails_loudly(self):
        """An absent committed recording is a CommandError, never a silent pass."""
        empty_dir = Path(tempfile.mkdtemp(prefix='aimms-no-fixture-'))
        with (
            mock.patch(
                'aichat.management.commands.seed_video_eval_fixtures._fixtures_dir',
                return_value=empty_dir,
            ),
            self.assertRaises(CommandError) as caught,
        ):
            call_command('seed_video_eval_fixtures', '--dry-run')
        self.assertIn('eval-hx200-seal-video.mp4', str(caught.exception))

    @override_settings(AIMMS_ATTACHMENT_RAG_ENABLED=True, AIMMS_MEDIA_RAG_ENABLED=True)
    def test_existing_seed_moves_off_the_frozen_photo_machine(self):
        """An early shared-machine seed is repaired and synchronously re-stamped."""
        from tasks.models import WorkOrder

        from assets.models import AssetMachine, get_default_client

        photo_machine = AssetMachine.objects.create(
            name='RAG Eval HX-200 Heat Exchanger',
            client=get_default_client(),
            serial='EVAL-HX200',
        )
        work_order = WorkOrder.objects.create(
            reference='WO-EVAL-HX200-VIDEO',
            title='Legacy shared-machine video fixture',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=photo_machine,
        )
        with (
            mock.patch(
                'ai.core.config.get_settings', return_value=_media_ai_settings()
            ),
            mock.patch('InvenTree.tasks.offload_task', return_value=True),
            mock.patch(
                'aichat.services.attachment_ingestion.run_ingest',
                return_value=SimpleNamespace(state='indexed', segment_count=3),
            ),
            mock.patch(
                'aichat.services.attachment_ingestion.'
                'restamp_work_order_media_client_codes'
            ) as restamp,
        ):
            call_command('seed_video_eval_fixtures', '--break-glass')

        work_order.refresh_from_db()
        self.assertNotEqual(work_order.machine_id, photo_machine.pk)
        self.assertEqual(
            work_order.machine.name, 'RAG Eval HX-200 Video Heat Exchanger'
        )
        self.assertEqual(work_order.machine.serial, 'EVAL-HX200-VIDEO')
        restamp.assert_called_once_with(work_order.pk)
