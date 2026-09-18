"""Projection drift, bounded unknowns and persistent recall-stop regressions."""

import os
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from ai.core.integrations.projection_gate import recall_allowed
from aichat.models import (
    AIUsageMonthlyAggregate,
    AttachmentChunk,
    AttachmentIngest,
    RagProjectionAudit,
    RagProjectionGate,
    RagProjectionRepair,
)
from aichat.services import projection_audit as service


class ProjectionAuditTests(TestCase):
    """No external provider or physical file is needed to exercise obligations."""

    def setUp(self):
        """Synthetic source authority and one Search document."""
        self.enterContext(mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': '0'}))
        self.enterContext(
            mock.patch.object(
                service,
                'get_settings',
                return_value=SimpleNamespace(single_site_policy_key='fixture'),
            )
        )
        self.enterContext(
            mock.patch(
                'aichat.services.attachment_ingestion.derive_client_codes',
                return_value=['allowed'],
            )
        )
        self.source = self.enterContext(
            mock.patch('common.models.Attachment.objects.filter')
        )
        self.source.return_value.first.return_value = SimpleNamespace(
            model_type='part', model_id=2
        )
        self.ingest = AttachmentIngest.objects.create(
            attachment_id=1234,
            model_type='part',
            model_id=2,
            source_sha256='a' * 64,
            pipeline='doc',
            state='indexed',
            search_index_name='fixture',
            claimed_at=timezone.now(),
        )
        AttachmentChunk.objects.create(
            ingest=self.ingest,
            chunk_index=0,
            content='PRIVATE FIXTURE',
            token_count=2,
            search_doc_id='fixture-doc',
        )
        self.doc = {
            'id': 'fixture-doc',
            'attachment_id': 1234,
            'model_type': 'part',
            'model_id': 2,
            'source_sha256': 'a' * 64,
            'client_codes': ['allowed'],
            'scope_key': 'fixture',
            'access_class': 'attachment_uploaded',
            'is_current': True,
        }
        self.client = mock.Mock()
        self.client.search.return_value = [self.doc]
        self.projection = mock.Mock(index_name='fixture')
        self.projection.client.return_value = self.client

    def audit(self):
        """Record an isolated bounded sample using the injected fake projection."""
        return service.verify_projection(
            corpus='attachment', record=True, projection=self.projection
        )

    def test_critical_scope_drift_latches_until_operator_release(self):
        """A subsequent clean sample cannot silently restore recall authority."""
        self.doc['client_codes'] = ['allowed', 'unauthorized']
        report = self.audit()
        self.assertEqual(report['critical'], 1)
        self.assertFalse(recall_allowed('attachment'))
        self.assertTrue(RagProjectionRepair.objects.filter(resolved=False).exists())
        self.assertNotIn('PRIVATE FIXTURE', str(report))
        self.assertNotIn('content', self.client.search.call_args.kwargs['select'])
        self.doc['client_codes'] = ['allowed']
        self.assertEqual(self.audit()['outcome'], 'clean_sample')
        self.assertFalse(recall_allowed('attachment'))
        gate = RagProjectionGate.objects.get(corpus='attachment')
        with self.assertRaises(ValueError):
            service.release_gate(
                corpus='attachment',
                expected_blocked_at=(
                    gate.updated_at - timedelta(seconds=1)
                ).isoformat(),
            )
        service.release_gate(
            corpus='attachment', expected_blocked_at=gate.updated_at.isoformat()
        )
        self.assertTrue(recall_allowed('attachment'))

    def test_deleted_source_and_provider_error_never_report_clean(self):
        """Residual deletion is critical; an unreadable projection is unknown."""
        self.source.return_value.first.return_value = None
        self.assertEqual(self.audit()['critical'], 1)
        self.client.search.side_effect = RuntimeError('PRIVATE PROVIDER ERROR')
        report = self.audit()
        self.assertEqual((report['outcome'], report['errors']), ('incomplete', 1))
        self.assertNotIn('PRIVATE', str(report))

    def test_detail_rollup_is_idempotent_and_preserves_open_repairs_and_gate(self):
        """Counts survive detail cleanup while unresolved operational stops remain."""
        self.doc['client_codes'] = ['unauthorized']
        self.audit()
        RagProjectionAudit.objects.update(
            created_at=timezone.now() - timedelta(days=100)
        )
        self.assertEqual(service.purge_audits()['rag_projection_audits'], 1)
        self.assertEqual(service.purge_audits()['rag_projection_audits'], 0)
        self.assertEqual(
            AIUsageMonthlyAggregate.objects.get(
                source='rag_projection_audits', dimension='attachment:critical'
            ).turn_count,
            1,
        )
        self.assertTrue(RagProjectionRepair.objects.filter(resolved=False).exists())
        self.assertTrue(RagProjectionGate.objects.get(corpus='attachment').blocked)
