"""Real future attribution across independently protected maintenance/Work edges."""

from datetime import date
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from tasks.integration_attachments import read_work_attachment_content
from tasks.integration_sources import read_work_order
from tasks.scope import scoped_work_orders

from assets import integration_work_links
from assets.integration_sources import read_maintenance_record
from assets.models import AssetMachine, AssetMaintenanceRecord, ClientScopeGrant
from common.models import Attachment
from company.models import Company
from integration_state import native_authority
from integration_state.tests.test_native_authority import NativeAuthorityFixture


class NativeMaintenanceRelationshipTests(NativeAuthorityFixture, TestCase):
    """No simulated ownership or service identity supplies either endpoint grant."""

    def setUp(self):
        """Keep the source on A and a natively shared, independently proven target on B."""
        super().setUp()
        self.target_grant = ClientScopeGrant.objects.create(
            user=self.owner, client=self.client_b
        )
        ClientScopeGrant.objects.create(user=self.reviewer, client=self.client_b)
        foreign = AssetMachine.objects.create(
            name='Synthetic foreign target machine', client=self.client_b
        )
        customer = Company.objects.create(
            name='Synthetic shared Work customer', is_customer=True
        )
        self.target = self.work(
            'Synthetic independently protected Work', machine=foreign, customer=customer
        )
        self.target_proof = self.attest(pk=self.target.pk, clients=(self.client_b.pk,))
        self.approve(self.target_proof)
        self.record = AssetMaintenanceRecord.objects.create(
            machine=self.machine,
            date=date(2025, 1, 2),
            summary='Synthetic independent relationship',
            work_order=self.target,
        )
        self.parent_proof = self.attest('maintenance', self.record.pk)
        self.approve(self.parent_proof)

    def link(self):
        """Read through the actual native independently authorized relationship."""
        return integration_work_links.read_maintenance_work_link(
            self.owner, environment=self.namespace.environment, record_pk=self.record.pk
        )

    def assert_parent_positive(self):
        """Retain the actual source positive when only the target is denied."""
        record = read_maintenance_record(
            self.owner, environment=self.namespace.environment, record_pk=self.record.pk
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.summary, self.record.summary)

    def test_shared_target_needs_its_own_current_grant(self):
        """Visible source plus native shared Work cannot borrow target grant A."""
        self.assertEqual(self.link().work.pk, self.target.pk)
        self.target_grant.delete()
        self.assert_parent_positive()
        self.assertTrue(
            scoped_work_orders(self.owner).filter(pk=self.target.pk).exists()
        )
        self.assertIsNone(
            read_work_order(
                self.owner,
                environment=self.namespace.environment,
                work_pk=self.target.pk,
            )
        )
        withheld = self.link()
        self.assertEqual(withheld.state, 'unknown')
        self.assertIsNone(withheld.work)
        self.target_grant = ClientScopeGrant.objects.create(
            user=self.owner, client=self.client_b
        )
        self.assertEqual(self.link().work.pk, self.target.pk)

    def test_target_withdrawal_preserves_the_parent_positive_without_identity(self):
        """Withdrawal of Work proof neither revokes parent proof nor exposes Work."""
        self.assertEqual(self.link().state, 'available')
        native_authority.withdraw_authority_request(
            self.request(),
            request_pk=self.target_proof.pk,
            expected_request_digest=self.target_proof.binding_digest,
            reason_reference='synthetic-target-withdrawal',
        )
        self.assert_parent_positive()
        withheld = self.link()
        self.assertEqual(withheld.state, 'unknown')
        self.assertIsNone(withheld.work)

    def test_relink_requires_a_fresh_parent_revision_and_preserves_real_null(self):
        """Current relinking cannot retain the old source's approval or fake absence."""
        AssetMaintenanceRecord.objects.filter(pk=self.record.pk).update(work_order=None)
        self.assertIsNone(self.link())
        self.approve(
            self.attest('maintenance', self.record.pk, key='synthetic-relinked-parent')
        )
        result = self.link()
        self.assertEqual(result.state, 'none')
        self.assertIsNone(result.work)

    def test_target_grant_revoked_during_projection_withholds_whole_observation(self):
        """Final endpoint checks detect a loss after a valid native constructor."""
        constructor = integration_work_links.MaintenanceWorkLinkSourceRecord

        def revoke(*args, **kwargs):
            result = constructor(*args, **kwargs)
            self.target_grant.delete()
            return result

        with patch.object(
            integration_work_links,
            'MaintenanceWorkLinkSourceRecord',
            side_effect=revoke,
        ):
            self.assertIsNone(self.link())
        self.assert_parent_positive()
        self.assertEqual(self.link().state, 'unknown')

    def test_authorized_edge_does_not_supply_original_content_attribution(self):
        """An admitted relationship cannot authorize a separately governed original."""
        attachment = Attachment(
            model_type='workorder',
            model_id=self.target.pk,
            attachment=SimpleUploadedFile(
                'synthetic-edge.txt', b'Synthetic independent original'
            ),
        )
        attachment.save(rebuild=False)
        self.addCleanup(attachment.attachment.delete, save=False)
        self.assertEqual(self.link().state, 'available')
        arguments = {
            'environment': self.namespace.environment,
            'attachment_pk': attachment.pk,
            'max_bytes': 100,
        }
        storage = attachment.attachment.storage
        with patch.object(storage, 'open', wraps=storage.open) as opened:
            self.assertIsNone(read_work_attachment_content(self.owner, **arguments))
            opened.assert_not_called()
        self.approve(
            self.attest('attachment', attachment.pk, clients=(self.client_b.pk,))
        )
        self.assertEqual(
            read_work_attachment_content(self.owner, **arguments).content,
            b'Synthetic independent original',
        )
