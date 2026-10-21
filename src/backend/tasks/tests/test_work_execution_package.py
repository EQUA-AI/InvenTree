"""P21 saved package projections do not establish physical authority."""

from types import SimpleNamespace

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from assets.models import AssetMachine, get_default_client
from InvenTree.unit_test import InvenTreeAPITestCase
from part.models import Part
from repair.models import ApprovedRepairScope, RepairPacket
from tasks.models import (
    Procedure,
    ProcedureRevision,
    WorkOrder,
    WorkOrderDeviation,
    WorkOrderProcedureApplication,
)
from tasks.work_execution.package import project


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver',
    AIMMS_PROCEDURES_ENABLED=True,
)
class PreparedPackageTest(InvenTreeAPITestCase):
    """Use the real overview, including native Work-scope enforcement."""

    roles = 'all'

    def setUp(self):
        """Create a saved package and a later mutable catalogue revision."""
        super().setUp()
        self.machine = AssetMachine.objects.create(
            name='Package machine', client=get_default_client()
        )
        self.work = WorkOrder.objects.create(title='Package work', machine=self.machine)
        self.packet = RepairPacket.objects.create(
            machine=self.machine, work_order=self.work, fault_summary='Inspect'
        )
        self.procedure = Procedure.objects.create(
            code='PACKAGE', name='Mutable catalogue name', created_by=self.user
        )
        self.revision = ProcedureRevision.objects.create(
            procedure=self.procedure,
            revision=2,
            work_order_type='corrective',
            created_by=self.user,
        )
        self.application = WorkOrderProcedureApplication.objects.create(
            work_order=self.work,
            revision=self.revision,
            applied_by=self.user,
            idempotency_key='package-fixture',
            snapshot_hash='a' * 64,
            snapshot={
                'procedure_id': self.procedure.pk,
                'revision_id': self.revision.pk,
                'revision': {'revision': 1, 'change_summary': 'Saved original'},
                'steps': [{'title': 'Original instruction', 'min_value': '0'}],
                'resources': [{'kind': 'tool', 'quantity': '1'}],
            },
        )

    def overview(self, code=200):
        """Read the same projection the real Work page consumes."""
        return self.get(
            reverse('kanban-card-overview', kwargs={'pk': self.work.pk}),
            expected_code=code,
        )

    def test_snapshot_not_live_catalogue_and_no_authority_inference(self):
        """An applied snapshot is preserved even if its catalogue differs."""
        package = self.overview().data['prepared_package']
        application = package['applications'][0]
        self.assertEqual(application['revision'], 1)
        self.assertEqual(application['steps'][0]['title'], 'Original instruction')
        self.assertEqual(application['steps'][0]['min_value'], '0')
        self.assertEqual(application['resources'][0]['quantity'], '1')
        self.assertTrue(package['physical_authority']['available'])
        self.assertFalse(package['physical_authority']['recorded'])
        self.assertEqual(package['physical_authority']['current_records'], [])
        self.assertFalse(package['affected_dependencies']['available'])

    def test_new_current_revision_changes_comparison_without_editing_application(self):
        """Publishing a newer catalogue revision cannot leave the old package current."""
        self.revision.status = 'published'
        self.revision.published_by = self.user
        self.revision.published_at = timezone.now()
        self.revision.save(update_fields=['status', 'published_by', 'published_at'])
        self.application.snapshot['revision']['revision'] = self.revision.revision
        self.application.save(update_fields=['snapshot'])
        self.procedure.current_revision = self.revision
        self.procedure.save(update_fields=['current_revision'])
        before = self.application.snapshot.copy()
        self.assertEqual(
            self.overview().data['prepared_package']['applications'][0]['drift_status'],
            'current',
        )
        self.revision.status = 'superseded'
        self.revision.save(update_fields=['status'])
        newer = ProcedureRevision.objects.create(
            procedure=self.procedure,
            revision=3,
            status='published',
            work_order_type='corrective',
            created_by=self.user,
            published_by=self.user,
            published_at=timezone.now(),
        )
        self.procedure.current_revision = newer
        self.procedure.save(update_fields=['current_revision'])
        row = self.overview().data['prepared_package']['applications'][0]
        self.assertEqual(row['drift_status'], 'different_revision')
        self.assertEqual(row['drift_basis'], 'catalogue_at_read')
        self.application.refresh_from_db()
        self.assertEqual(self.application.snapshot, before)
        self.assertEqual(self.application.drift_status, 'current')

    def test_default_or_inconsistent_catalogue_state_is_not_current(self):
        """An unevaluated default and inconsistent snapshot never supply reassurance."""
        row = self.overview().data['prepared_package']['applications'][0]
        self.assertEqual(row['drift_status'], 'not_evaluated')
        self.procedure.current_revision = self.revision
        self.procedure.save(update_fields=['current_revision'])
        self.assertEqual(
            self.overview().data['prepared_package']['applications'][0]['drift_status'],
            'not_evaluated',
        )
        self.application.snapshot['revision']['revision'] = self.revision.revision
        self.application.save(update_fields=['snapshot'])
        self.assertEqual(
            self.overview().data['prepared_package']['applications'][0]['drift_status'],
            'revision_not_published',
        )

    def test_catalogue_name_is_separate_from_the_applied_snapshot(self):
        """The current catalogue label aids reading without changing saved content."""
        before = self.application.snapshot.copy()
        self.procedure.code = 'RENAMED-PACKAGE'
        self.procedure.name = 'Current catalogue procedure title'
        self.procedure.save(update_fields=['code', 'name'])
        row = self.overview().data['prepared_package']['applications'][0]
        self.assertEqual(row['current_catalogue_code'], self.procedure.code)
        self.assertEqual(row['current_catalogue_name'], self.procedure.name)
        self.application.refresh_from_db()
        self.assertEqual(self.application.snapshot, before)

    def test_scope_revisions_and_unresolved_deviation_are_retained(self):
        """Both scope versions and a deviation retain actors and reason."""
        ApprovedRepairScope.objects.create(
            packet=self.packet,
            version=1,
            verified_cause='Original basis',
            superseded_at=timezone.now(),
        )
        ApprovedRepairScope.objects.create(
            packet=self.packet,
            version=2,
            verified_cause='Changed basis',
            approved_by=self.user,
            approval_note='Retained reason',
        )
        WorkOrderDeviation.objects.create(
            work_order=self.work,
            category='changed_condition',
            reason='Access unavailable',
            actor=self.user,
            expected={'access': True},
            actual={'access': False},
        )
        package = self.overview().data['prepared_package']
        self.assertEqual(
            [scope['current'] for scope in package['scopes']], [True, False]
        )
        self.assertEqual(package['scopes'][0]['note'], 'Retained reason')
        deviation = package['deviations'][0]
        self.assertEqual(deviation['reason'], 'Access unavailable')
        self.assertIsNotNone(deviation['recorded_by'])
        self.assertIsNone(deviation['resolved_at'])

    @override_settings(AIMMS_PROCEDURES_ENABLED=False)
    def test_disabled_source_is_not_an_empty_success(self):
        """No applied content or create control leaks through the flag."""
        package = self.overview().data['prepared_package']
        self.assertEqual(package['procedures_state'], 'not_enabled')
        self.assertEqual(package['applications'], [])
        self.assertEqual(package['deviations'], [])
        self.assertFalse(package['can_record_deviation'])

    def test_repeated_reads_do_not_write(self):
        """Overview reads cannot apply, approve, resolve or complete anything."""
        before = (self.work.updated_at, self.application.snapshot_hash)
        events = self.work.events.count()
        self.overview()
        self.overview()
        self.work.refresh_from_db()
        self.application.refresh_from_db()
        self.assertEqual((self.work.updated_at, self.application.snapshot_hash), before)
        self.assertEqual(self.work.events.count(), events)
        self.assertEqual(self.work.deviations.count(), 0)

    def test_parent_scope_is_required(self):
        """A foreign explicit Work customer denies all child package data."""
        from company.models import Company

        self.work.customer = Company.objects.create(name='Foreign package customer')
        self.work.save(update_fields=['customer'])
        self.overview(code=404)

    def test_resource_part_name_is_catalogue_label_without_mutating_snapshot(self):
        """Part readers get a native name; the saved requirement stays original."""
        part = Part.objects.create(name='Calibrated vibration instrument')
        self.application.snapshot['resources'][0]['part_id'] = part.pk
        self.application.save()
        resource = self.overview().data['prepared_package']['applications'][0][
            'resources'
        ][0]
        self.assertEqual(resource['part_id'], part.pk)
        self.assertEqual(resource['part_name'], part.name)
        self.application.refresh_from_db()
        self.assertNotIn('part_name', self.application.snapshot['resources'][0])

    def test_non_part_reader_or_missing_part_has_no_catalogue_name(self):
        """A Work read cannot grant catalogue visibility or invent a deleted name."""
        part = Part.objects.create(name='Restricted catalogue label')
        self.application.snapshot['resources'][0]['part_id'] = part.pk
        self.application.save()
        actor = SimpleNamespace(is_authenticated=True, has_perm=lambda name: False)
        resource = project(self.work, actor)['applications'][0]['resources'][0]
        self.assertIsNone(resource['part_name'])
        part.active = False
        part.save()
        part.delete()
        actor = SimpleNamespace(is_authenticated=True, has_perm=lambda name: True)
        resource = project(self.work, actor)['applications'][0]['resources'][0]
        self.assertIsNone(resource['part_name'])
