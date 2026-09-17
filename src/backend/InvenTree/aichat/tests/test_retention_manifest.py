"""Manifest and residual-evidence contracts; authored for deferred validation."""

import io
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from aichat.models import (
    AIRetentionOutbox,
    ChatMessage,
    ChatThread,
    ChatThreadTombstone,
)
from aichat.services import retention
from aichat.services.retention_audit import audit_deleted_threads
from aichat.tests.test_retention_reconciliation import RetentionEnvMixin


class RetentionManifestTests(RetentionEnvMixin, TestCase):
    """A deletion cannot be complete while its registered content remains."""

    def setUp(self):
        """Keep filesystem probes and extension registrations disposable."""
        self.uploads = tempfile.TemporaryDirectory()
        self.addCleanup(self.uploads.cleanup)
        patcher = mock.patch.object(
            retention, '_upload_root', return_value=Path(self.uploads.name)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        for mapping in (retention.OUTBOX_KINDS, retention.THREAD_DERIVATIVES._entries):
            patcher = mock.patch.dict(mapping)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_installed_models_have_explicit_coverage(self):
        """Future direct thread fields require registration before shipping."""
        self.assertEqual(retention.THREAD_DERIVATIVES.uncovered_models(), [])
        for entry in retention.THREAD_DERIVATIVES.entries:
            self.assertIn(entry.outbox_kind, retention.OUTBOX_KINDS)
            self.assertTrue(callable(entry.purge))
            self.assertTrue(callable(entry.probe))
        self.assertTrue(all(retention.THREAD_DERIVATIVES.exemptions.values()))

    def test_unregistered_model_blocks_before_first_child_delete(self):
        """An incomplete manifest cannot silently discard the parent only."""
        _, repository, thread = self.build_thread('missing-registration')
        before = ChatMessage.objects.filter(thread=thread).count()
        with mock.patch.object(
            retention.THREAD_DERIVATIVES,
            'uncovered_models',
            return_value=['memory.fixture'],
        ):
            with self.assertRaises(retention.DerivativeRegistrationError):
                repository.delete(thread.pk)
        self.assertEqual(ChatMessage.objects.filter(thread=thread).count(), before)
        self.assertFalse(ChatThreadTombstone.objects.exists())

    def test_registration_is_atomic_on_duplicate_kind(self):
        """A collision cannot install half of a deletion manifest entry."""
        kinds = {
            'taken': retention.OutboxKind('taken', lambda ref: None, lambda ref: 0)
        }
        registry = retention.ThreadDerivativeRegistry(kinds)
        with self.assertRaises(ValueError):
            registry.register(
                retention.ThreadDerivative(
                    'fixture',
                    ('memory.fixture',),
                    lambda ref: None,
                    lambda ref: 0,
                    'taken',
                )
            )
        self.assertEqual(registry.entries, ())
        self.assertEqual(set(kinds), {'taken'})

    def test_introspection_detects_a_new_plain_thread_reference(self):
        """Plain ids outside the FK cascade still require an explicit lifecycle."""
        registry = retention.ThreadDerivativeRegistry({})
        model = SimpleNamespace(
            _meta=SimpleNamespace(
                app_label='memory',
                label_lower='memory.fixture',
                get_fields=lambda: [
                    SimpleNamespace(
                        name='thread_id', related_model=None, auto_created=False
                    )
                ],
            )
        )
        self.assertEqual(registry.uncovered_models([model]), ['memory.fixture'])
        registry.exempt('memory.fixture', 'Disposable fixture with no content.')
        self.assertEqual(registry.uncovered_models([model]), [])

    def test_invalid_probe_results_cannot_count_as_zero(self):
        """Unknown, boolean and negative results cannot qualify cleanup."""
        for value in (None, False, -1, '0'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                retention.checked_residual(lambda ref, result=value: result, 'fixture')

    def test_failed_derivative_is_retryable_after_parent_deletion(self):
        """A dangling extension retains a durable cleanup obligation."""
        content = {'remaining': 1, 'fail': True}

        def purge(reference):
            if content['fail']:
                raise RuntimeError('PRIVATE FAILURE TEXT')
            content['remaining'] = 0

        retention.THREAD_DERIVATIVES.register(
            retention.ThreadDerivative(
                'fixture', (), purge, lambda ref: content['remaining'], 'thread_fixture'
            )
        )
        _, repository, thread = self.build_thread(
            'failed-derivative', voice=True, proposals=True
        )
        self.assertEqual(repository.delete(thread.pk)['status'], 'purge_incomplete')
        self.assertFalse(ChatThread.objects.filter(pk=thread.pk).exists())
        self.assertTrue(
            AIRetentionOutbox.objects.filter(
                kind='thread_fixture', state='pending'
            ).exists()
        )
        report = audit_deleted_threads()
        self.assertEqual(report['residual_by_kind']['thread_fixture'], 1)
        self.assertEqual(report['status'], 'incomplete')
        self.assertNotIn('PRIVATE', json.dumps(report))
        content['fail'] = False
        self.assertEqual(repository.delete(thread.pk)['status'], 'deleted')
        self.assertEqual(audit_deleted_threads()['status'], 'clean_sample')

    def test_outbox_cannot_delete_live_thread_derivatives(self):
        """A mistakenly queued reference must not erase an active transcript."""
        _, _, thread = self.build_thread('live-thread')
        before = ChatMessage.objects.filter(thread=thread).count()
        retention.enqueue_outbox('thread_messages', thread.pk)
        self.assertEqual(retention.process_retention_outbox()['done'], 0)
        self.assertEqual(ChatMessage.objects.filter(thread=thread).count(), before)

    def test_successful_handler_with_residual_is_not_marked_done(self):
        """Provider acknowledgement alone is not deletion evidence."""
        retention.register_outbox_kind(
            retention.OutboxKind('fixture', lambda ref: None, lambda ref: 1)
        )
        retention.enqueue_outbox('fixture', 'fixture-reference')
        report = retention.process_retention_outbox()
        self.assertEqual(report['done'], 0)
        self.assertEqual(report['retried'], 1)
        self.assertEqual(
            AIRetentionOutbox.objects.get().last_error_code, 'residual_remaining'
        )

    def test_audit_is_read_only_and_probe_failures_are_unknown(self):
        """Errors cannot become zero counts or leak provider/content strings."""
        _, repository, thread = self.build_thread('audit-probe')
        repository.delete(thread.pk)
        retention.register_outbox_kind(
            retention.OutboxKind(
                'broken',
                lambda ref: None,
                mock.Mock(side_effect=OSError('PRIVATE PROVIDER PATH')),
            )
        )
        with CaptureQueriesContext(connection) as queries:
            report = audit_deleted_threads()
        self.assertFalse(
            any(
                query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))
                for query in queries
            )
        )
        self.assertIsNone(report['residual_by_kind']['broken'])
        self.assertEqual(report['probe_errors_by_kind']['broken'], 1)
        self.assertEqual(report['status'], 'incomplete')
        self.assertNotIn('PRIVATE', json.dumps(report))
        self.assertNotIn(thread.pk, json.dumps(report))

    def test_no_samples_cannot_pass_cli_gate(self):
        """Missing evidence and invalid sample sizes do not qualify deletion."""
        self.assertEqual(audit_deleted_threads()['status'], 'no_samples')
        with self.assertRaises(CommandError):
            call_command(
                'retention_audit', '--fail-on-incomplete', stdout=io.StringIO()
            )
        for sample in (0, -1, 1001, True):
            with self.subTest(sample=sample), self.assertRaises(ValueError):
                audit_deleted_threads(sample=sample)

    def test_unfinished_cleanup_pins_its_deletion_receipt(self):
        """Aged tombstones must not discard authorization for outstanding retry."""
        _, repository, thread = self.build_thread('pinned-tombstone')
        repository.delete(thread.pk)
        ChatThreadTombstone.objects.filter(thread_id=thread.pk).update(
            deleted_at=timezone.now()
            - timedelta(days=retention.RETENTION_TOMBSTONE_DAYS + 1)
        )
        retention.enqueue_outbox('thread_proposals', thread.pk)
        self.assertEqual(retention.purge_tombstones()['tombstones'], 0)
        repository.delete(thread.pk)
        self.assertGreater(retention.purge_tombstones()['tombstones'], 0)

    def test_recorded_evidence_is_available_to_operations_report(self):
        """Recording is explicit and status preserves the evidence timestamp."""
        with mock.patch('common.models.InvenTreeSetting.set_setting') as save:
            report = audit_deleted_threads(record=True)
        save.assert_called_once()
        with mock.patch(
            'common.models.InvenTreeSetting.get_setting',
            return_value=json.dumps(report),
        ):
            self.assertEqual(retention.retention_status()['deletion_audit'], report)
