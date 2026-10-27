"""Thread cleanup preserves owner erasure and protected voice evidence."""

import json
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from aichat.models import AIRetentionOutbox, ChatThread
from aichat.services import client_memory_erasure, retention
from aichat.tests.test_retention_reconciliation import RetentionEnvMixin
from common.models import InvenTreeSetting
from voice.models import VoiceOperation, VoiceSession


class RetentionScopeAndVoiceTests(RetentionEnvMixin, TestCase):
    """Keep the independent retention guarantees valid in one native registry."""

    def setUp(self):
        """Use only owned upload storage."""
        uploads = tempfile.TemporaryDirectory()
        self.addCleanup(uploads.cleanup)
        patcher = mock.patch.object(
            retention, '_upload_root', return_value=Path(uploads.name)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_large_receipt_remains_within_native_setting_limit(self):
        """Detailed reports must not lose the operations receipt on PostgreSQL."""
        stamp = timezone.now().isoformat()
        report = {
            'started_at': stamp,
            'finished_at': stamp,
            'dry_run': False,
            'errors': {},
            'families': {'voice': {'detail': 'x' * 4000}},
        }
        retention._write_last_run(report)
        raw = InvenTreeSetting.get_setting(retention.LAST_RUN_SETTING, '')
        self.assertTrue(raw)
        self.assertLessEqual(
            len(raw), InvenTreeSetting._meta.get_field('value').max_length
        )
        self.assertEqual(
            json.loads(raw),
            {
                'started_at': stamp,
                'finished_at': stamp,
                'dry_run': False,
                'errors': {},
                'families': ['voice'],
                'detail': 'omitted',
            },
        )

    def test_thread_receipt_neither_executes_nor_probes_owner_erasure(self):
        """A colliding reference is not authority over a different namespace."""
        _, repository, thread = self.build_thread('namespace-owner')
        handlers, probes, replacements = {}, {}, {}
        kinds = ('memory_owner_purge', client_memory_erasure.KIND)
        for name in kinds:
            registered = retention.OUTBOX_KINDS[name]
            self.assertFalse(registered.thread_scoped)
            handlers[name] = mock.Mock()
            probes[name] = mock.Mock(return_value=1)
            replacements[name] = replace(
                registered, handler=handlers[name], probe=probes[name]
            )
            retention.enqueue_outbox(name, thread.pk)

        with mock.patch.dict(retention.OUTBOX_KINDS, replacements):
            receipt = repository.delete(thread.pk)
            self.assertEqual(receipt['status'], 'deleted')
            self.assertEqual(retention.thread_purge_receipt(thread.pk)['status'], 'deleted')

        for name in kinds:
            handlers[name].assert_not_called()
            probes[name].assert_not_called()
            self.assertEqual(
                AIRetentionOutbox.objects.get(
                    kind=name, reference=thread.pk
                ).state,
                'pending',
            )

    def test_protected_voice_session_does_not_block_unrelated_session_cleanup(self):
        """A protected operational receipt keeps only its own voice session."""
        user, repository, thread = self.build_thread('voice-retention-owner')
        protected = self._add_voice(user, thread.pk)
        disposable = self._add_voice(user, thread.pk)
        operation = VoiceOperation.objects.create(
            session=protected,
            decision_id='retention-protected-decision',
            source_id='retention-fixture',
            action='work_order.update',
            target_label='Fixture work',
        )

        receipt = repository.delete(thread.pk)

        self.assertEqual(receipt['status'], 'purge_incomplete')
        self.assertFalse(ChatThread.objects.filter(pk=thread.pk).exists())
        self.assertTrue(VoiceSession.objects.filter(pk=protected.pk).exists())
        self.assertFalse(VoiceSession.objects.filter(pk=disposable.pk).exists())
        self.assertTrue(VoiceOperation.objects.filter(pk=operation.pk).exists())
        self.assertEqual(
            retention.thread_purge_receipt(thread.pk)['status'], 'purge_incomplete'
        )
