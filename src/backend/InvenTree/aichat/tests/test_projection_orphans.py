"""Authored reverse-audit coverage; fake Search never contacts a provider."""

import os
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase

from aichat.models import RagProjectionAudit, RagProjectionGate, RagProjectionOrphan
from aichat.services import projection_orphans as service
from aichat.services.projection_audit import release_gate


class ReverseProjectionTests(TestCase):
    """No-ledger obligations must survive and block unrelated clean samples."""

    def setUp(self):
        """Only metadata-shaped fake provider responses are used."""
        self.enterContext(mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': '0'}))
        self.source = self.enterContext(
            mock.patch('common.models.Attachment.objects.filter')
        )
        self.source.return_value.first.return_value = SimpleNamespace(
            model_type='part', model_id=1
        )
        self.projection = mock.Mock(index_name='fixture')
        self.client = self.projection.client.return_value
        self.row = {'id': 'orphan-1', 'attachment_id': 987654}
        self.client.search.return_value = [self.row]

    def test_missing_ledger_blocks_release_after_unrelated_clean_sample(self):
        """Forward-audit success cannot discard an independently found orphan."""
        result = service.scan_orphans(
            corpus='attachment', record=True, projection=self.projection
        )
        self.assertEqual(result['critical'], 1)
        finding = RagProjectionOrphan.objects.get()
        self.assertEqual(finding.reason, 'missing_ledger')
        self.assertNotIn('content', self.client.search.call_args.kwargs['select'])
        gate = RagProjectionGate.objects.get(corpus='attachment')
        RagProjectionAudit.objects.create(corpus='attachment', outcome='clean_sample')
        with self.assertRaises(ValueError):
            release_gate(
                corpus='attachment', expected_blocked_at=gate.updated_at.isoformat()
            )

    def test_provider_failure_or_changed_index_does_not_resolve(self):
        """No ledger FK, provider exception or new index erases the finding."""
        service.scan_orphans(
            corpus='attachment', record=True, projection=self.projection
        )
        finding = RagProjectionOrphan.objects.get()
        self.client.get_document.side_effect = RuntimeError('PRIVATE')
        with self.assertRaises(RuntimeError):
            service.recheck_orphan(identity=finding.pk, projection=self.projection)
        finding.refresh_from_db()
        self.assertFalse(finding.resolved)
        self.projection.index_name = 'different'
        with self.assertRaises(ValueError):
            service.recheck_orphan(identity=finding.pk, projection=self.projection)

    def test_exact_absence_resolves_only_if_same_index_still_answers(self):
        """An absent index's 404 is not evidence of an absent document."""
        from azure.core.exceptions import ResourceNotFoundError

        service.scan_orphans(
            corpus='attachment', record=True, projection=self.projection
        )
        finding = RagProjectionOrphan.objects.get()
        self.client.get_document.side_effect = ResourceNotFoundError()
        self.client.search.side_effect = ResourceNotFoundError()
        with self.assertRaises(ResourceNotFoundError):
            service.recheck_orphan(identity=finding.pk, projection=self.projection)
        self.client.search.side_effect = None
        self.client.search.return_value = []
        result = service.recheck_orphan(identity=finding.pk, projection=self.projection)
        self.assertEqual(result['status'], 'resolved')
        self.assertTrue(RagProjectionGate.objects.get().blocked)
