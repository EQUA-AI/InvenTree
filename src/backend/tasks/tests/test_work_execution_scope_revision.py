"""P43: retained facts cannot substitute for missing revision dependencies."""

import uuid

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from assets.models import AssetMachine, get_default_client
from InvenTree.unit_test import InvenTreeAPITestCase
from repair.investigation import approve_repair_scope
from repair.models import ApprovedRepairScope, RepairInvestigationFinding, RepairPacket
from tasks.investigation_models import InvestigationArtifact, InvestigationRequest
from tasks.models import WorkOrder, WorkOrderEvent
from tasks.services.investigation_requests import command


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver',
    AIMMS_PROCEDURES_ENABLED=True,
)
class ScopeRevisionProjectionTest(InvenTreeAPITestCase):
    """Use real native scope/request writers and the authorized overview."""

    roles = 'all'

    def setUp(self):
        """One work/packet with native immutable scope versions."""
        super().setUp()
        self.assignRole('work_order.change')
        self.machine = AssetMachine.objects.create(
            name='Revision machine', client=get_default_client()
        )
        self.work = WorkOrder.objects.create(
            title='Revision work', machine=self.machine
        )
        self.packet = RepairPacket.objects.create(
            machine=self.machine, work_order=self.work, fault_summary='Synthetic noise'
        )
        self.before = approve_repair_scope(
            self.packet,
            scope_lines=[{'sequence': 1, 'action': 'Synthetic inspection'}],
            verified_cause='Original basis',
            actor=self.user,
        )
        self.after = approve_repair_scope(
            self.packet,
            scope_lines=[
                {'sequence': 1, 'action': 'Synthetic revised inspection'},
                {'sequence': 2, 'action': 'Synthetic follow-up'},
            ],
            verified_cause='Revised basis',
            actor=self.user,
            note='Recorded approval note',
        )

    def package(self):
        """Read the exact parent-scoped package projection."""
        return self.get(
            reverse('kanban-card-overview', kwargs={'pk': self.work.pk}),
            expected_code=200,
        ).data['prepared_package']

    def test_before_after_versions_keep_notes_without_inventing_impact(self):
        """A plan comparison is not a task mapping, reason or expanded permit."""
        package = self.package()
        revision = package['revision_review']
        self.assertEqual(revision['state'], 'revised')
        self.assertEqual(revision['current_scope_id'], self.after.pk)
        self.assertEqual(
            revision['comparisons'],
            [
                {
                    'before_scope_id': self.before.pk,
                    'after_scope_id': self.after.pk,
                    'basis': 'retained_version_order',
                }
            ],
        )
        self.assertEqual(package['scopes'][0]['note'], 'Recorded approval note')
        self.assertIsNotNone(package['scopes'][1]['superseded_at'])
        self.assertEqual(
            [item['kind'] for item in revision['dependency_impact']],
            ['tasks', 'materials', 'resources', 'permissions', 'criteria'],
        )
        self.assertTrue(
            all(not item['available'] for item in revision['dependency_impact'])
        )
        self.assertFalse(revision['evidence_validity']['available'])

    def test_request_revision_preserves_before_after_and_original_evidence(self):
        """A revised check keeps the same request and its previously saved evidence."""

        def apply(operation, fields, key, row=None):
            return command(
                work_order_id=self.work.pk,
                actor=self.user,
                expected_version=self.work.lifecycle_version,
                idempotency_key=key,
                operation=operation,
                fields=fields,
                request_id=row.pk if row else None,
                request_version=row.version if row else None,
            )

        apply(
            'create',
            {
                'purpose': 'Original decision',
                'next_check': 'First instrument',
                'required_artifacts': [],
            },
            'create',
        )
        row = InvestigationRequest.objects.get(work_order=self.work)
        apply(
            'artifact',
            {'kind': 'observation', 'payload': {'text': 'Retained observation'}},
            'evidence',
            row,
        )
        row.refresh_from_db()
        artifact = InvestigationArtifact.objects.get(request=row)
        finding = RepairInvestigationFinding.objects.create(
            packet=self.packet,
            finding_key='revision-source',
            category='inspection',
            observation='Recorded revision evidence',
            recorded_by=self.user,
        )
        apply(
            'revise',
            {
                'next_check': 'Second instrument',
                'blocked_reason': 'First instrument unavailable',
                'source_finding_id': finding.pk,
            },
            'revise',
            row,
        )
        revision = self.package()['revision_review']['request_revisions'][0]
        self.assertEqual(revision['request_id'], row.pk)
        self.assertEqual(revision['before']['next_check'], 'First instrument')
        self.assertEqual(revision['after']['next_check'], 'Second instrument')
        self.assertEqual(
            (revision['before']['version'], revision['after']['version']), (2, 3)
        )
        self.assertEqual(revision['retained_evidence_ids'], [artifact.pk])
        self.assertIsNone(revision['before']['source_finding'])
        self.assertEqual(revision['after']['source_finding'], finding.pk)
        self.assertIsNone(revision['scope_link'])
        self.assertIsNone(revision['revision_reason'])
        self.assertEqual(
            InvestigationArtifact.objects.get(pk=artifact.pk).payload,
            {'text': 'Retained observation'},
        )

    def test_no_current_or_conflicting_current_scope_never_selects_obsolete(self):
        """Bad legacy markers do not make an older approval effective silently."""
        ApprovedRepairScope.objects.filter(pk=self.after.pk).update(
            superseded_at=timezone.now()
        )
        self.assertEqual(self.package()['revision_review']['state'], 'no_current_scope')
        self.assertIsNone(self.package()['revision_review']['current_scope_id'])
        ApprovedRepairScope.objects.filter(pk=self.before.pk).update(superseded_at=None)
        self.assertEqual(
            self.package()['revision_review']['state'], 'conflicting_scope'
        )
        overview = self.get(
            reverse('kanban-card-overview', kwargs={'pk': self.work.pk}),
            expected_code=200,
        ).data
        self.assertIsNone(overview['investigation']['intervention'])
        self.assertIsNone(overview['repair_packet']['approved_scope'])
        self.assertIsNone(self.package()['revision_review']['current_scope_id'])
        ApprovedRepairScope.objects.filter(pk=self.after.pk).update(superseded_at=None)
        self.assertEqual(
            self.package()['revision_review']['state'], 'conflicting_scope'
        )

    def test_foreign_or_malformed_audit_snapshot_is_not_projected(self):
        """A corrupted event cannot attach a foreign request to this work."""
        foreign = WorkOrder.objects.create(
            title='Foreign revision', machine=self.machine
        )
        row = InvestigationRequest.objects.create(
            work_order=foreign,
            purpose='Foreign secret',
            next_check='Foreign check',
            created_by=self.user,
            updated_by=self.user,
        )
        for result in (
            {
                'before': {'id': row.pk, 'work_order': foreign.pk},
                'request': {'id': row.pk, 'work_order': foreign.pk},
            },
            ['malformed'],
        ):
            WorkOrderEvent.objects.create(
                work_order=self.work,
                event_type='investigation_revise',
                correlation_id=uuid.uuid4(),
                metadata={'result_metadata': result},
            )
        self.assertEqual(self.package()['revision_review']['request_revisions'], [])

    def test_repeated_projection_retains_source_versions_and_lifecycle(self):
        """Reading a comparison never invalidates evidence, scopes or sibling work."""
        sibling = WorkOrder.objects.create(
            title='Independent work',
            machine=self.machine,
            lifecycle_status='in_progress',
        )
        before = (
            self.work.lifecycle_version,
            self.work.updated_at,
            self.packet.events.count(),
            self.work.events.count(),
        )
        self.package()
        self.package()
        self.work.refresh_from_db()
        sibling.refresh_from_db()
        self.assertEqual(
            (
                self.work.lifecycle_version,
                self.work.updated_at,
                self.packet.events.count(),
                self.work.events.count(),
            ),
            before,
        )
        self.assertEqual(sibling.lifecycle_status, 'in_progress')
        self.assertEqual(self.packet.approved_scopes.count(), 2)

    def test_foreign_parent_scope_denies_the_entire_projection(self):
        """Child revision facts retain the canonical parent's maintenance boundary."""
        from company.models import Company

        self.work.customer = Company.objects.create(
            name='Foreign revision customer', is_customer=True
        )
        self.work.save(update_fields=['customer'])
        self.get(
            reverse('kanban-card-overview', kwargs={'pk': self.work.pk}),
            expected_code=404,
        )

    @override_settings(AIMMS_PROCEDURES_ENABLED=False)
    def test_disabled_procedures_do_not_discard_scope_history(self):
        """Independent scope facts survive; no disabled execution content is supplied."""
        package = self.package()
        self.assertEqual(package['procedures_state'], 'not_enabled')
        self.assertEqual(package['applications'], [])
        self.assertEqual(package['revision_review']['state'], 'revised')
