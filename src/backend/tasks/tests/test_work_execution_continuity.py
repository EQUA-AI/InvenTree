"""P44: record transitions preserve evidence and never accept a handover."""

import json
import uuid

from django.contrib.auth.models import Permission
from django.test import override_settings
from django.urls import reverse

from assets.models import AssetMachine, get_default_client
from company.models import Company
from InvenTree.unit_test import InvenTreeAPITestCase
from repair.models import RepairPacket
from tasks.investigation_models import InvestigationArtifact, InvestigationRequest
from tasks.models import (
    WorkOrder,
    WorkOrderCommand,
    WorkOrderDependency,
    WorkOrderEvent,
)


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver',
    AIMMS_WORK_ORDERS_ENABLED=True,
)
class WorkContinuityProjectionTest(InvenTreeAPITestCase):
    """Real scoped overview and existing Hold/Resume services on offline SQLite."""

    roles = 'all'

    def setUp(self):
        """One in-progress standalone Work and one independently owned sibling."""
        super().setUp()
        self.machine = AssetMachine.objects.create(
            name='Continuity machine', client=get_default_client()
        )
        self.work = WorkOrder.objects.create(
            title='Continuity work',
            machine=self.machine,
            assigned_to=self.user,
            lifecycle_status='in_progress',
        )
        self.sibling = WorkOrder.objects.create(
            title='Independent sibling',
            machine=self.machine,
            lifecycle_status='in_progress',
            lifecycle_version=4,
        )

    def execute_permission(self):
        """Grant the existing execution permission, separately from CRUD roles."""
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label='tasks', codename='execute_workorder'
            )
        )
        for name in ('_perm_cache', '_user_perm_cache', '_group_perm_cache'):
            self.user.__dict__.pop(name, None)

    def overview(self):
        """Read this exact authorized Work without a command."""
        return self.get(
            reverse('kanban-card-overview', kwargs={'pk': self.work.pk}),
            expected_code=200,
        ).data

    def post_command(
        self, action, version, key, reason='Synthetic hold reason', expected=200
    ):
        """Use the native command's exact lifecycle version and idempotency key."""
        response = self.client.post(
            f'/api/tasks/work-orders/{self.work.pk}/{action}/',
            {'expected_version': version, 'idempotency_key': key, 'reason': reason},
            format='json',
        )
        body = getattr(response, 'data', None)
        self.assertEqual(response.status_code, expected, body)
        return body

    def test_native_hold_resume_replay_keeps_request_evidence_and_sibling_separate(
        self,
    ):
        """One record transition does not resolve checks or alter another order."""
        self.execute_permission()
        request = InvestigationRequest.objects.create(
            work_order=self.work,
            purpose='Synthetic instrument dependency',
            next_check='Synthetic replacement instrument',
            status='cannot_perform',
            blocked_reason='Instrument unavailable',
            owner=self.user,
            created_by=self.user,
            updated_by=self.user,
        )
        sibling_request = InvestigationRequest.objects.create(
            work_order=self.sibling,
            purpose='Independent sibling decision',
            next_check='Separate owned check',
            created_by=self.user,
            updated_by=self.user,
        )
        artifact = InvestigationArtifact.objects.create(
            request=request,
            kind='observation',
            payload={'text': 'Retained original'},
            recorded_by=self.user,
        )
        first = self.post_command('hold', 1, 'hold-original')
        self.assertEqual(self.post_command('hold', 1, 'hold-original'), first)
        held = self.overview()['work_continuity']
        self.assertEqual(held['current_hold']['event_id'], first['event_id'])
        self.assertEqual(held['current_hold']['reason'], 'Synthetic hold reason')
        self.assertEqual(
            held['current_hold']['recorded_by'],
            self.user.get_full_name().strip() or self.user.username,
        )
        resumed = self.post_command(
            'resume', 2, 'resume-original', reason='Current record requirements checked'
        )
        self.assertEqual(
            self.post_command(
                'resume',
                2,
                'resume-original',
                reason='Current record requirements checked',
            ),
            resumed,
        )
        projection = self.overview()['work_continuity']
        self.assertIsNone(projection['current_hold'])
        self.assertEqual(
            [item['action'] for item in projection['history']], ['resume', 'hold']
        )
        self.assertFalse(projection['accepted_handover']['available'])
        self.assertFalse(projection['assignment_acceptance']['available'])
        request.refresh_from_db()
        self.sibling.refresh_from_db()
        sibling_request.refresh_from_db()
        self.assertEqual((sibling_request.version, sibling_request.status), (1, 'open'))
        self.assertEqual(
            (request.version, request.status, request.blocked_reason),
            (1, 'cannot_perform', 'Instrument unavailable'),
        )
        self.assertEqual(
            InvestigationArtifact.objects.get(pk=artifact.pk).payload,
            {'text': 'Retained original'},
        )
        self.assertEqual(
            (self.sibling.lifecycle_status, self.sibling.lifecycle_version),
            ('in_progress', 4),
        )
        self.assertEqual(
            WorkOrderCommand.objects.filter(work_order=self.work).count(), 2
        )
        self.assertEqual(self.work.events.count(), 2)

    def test_assignment_and_repeated_reads_never_accept_handover_or_start_time(self):
        """Opening a brief cannot establish acceptance, labor or physical state."""
        before = (self.work.lifecycle_version, self.work.updated_at)
        for _ in range(2):
            value = self.overview()['work_continuity']
            self.assertFalse(value['accepted_handover']['available'])
            self.assertFalse(value['assignment_acceptance']['available'])
            self.assertFalse(value['physical_service_state']['available'])
            self.assertFalse(value['cross_crew_restrictions']['available'])
        self.work.refresh_from_db()
        self.assertEqual((self.work.lifecycle_version, self.work.updated_at), before)
        self.assertIsNone(self.work.actual_started_at)
        self.assertEqual(self.work.events.count(), 0)

    def test_commands_follow_existing_execute_permission_flag_and_owner(self):
        """An overview reader does not inherit a command or packet authority."""
        self.assertFalse(self.overview()['work_continuity']['commands']['hold'])
        self.post_command('hold', 1, 'reader-hold', expected=403)
        self.execute_permission()
        self.assertTrue(self.overview()['work_continuity']['commands']['hold'])
        with override_settings(AIMMS_WORK_ORDERS_ENABLED=False):
            commands = self.overview()['work_continuity']['commands']
            self.assertEqual(commands['unavailable_reason'], 'api_disabled')
            self.assertFalse(commands['hold'])
            self.post_command('hold', 1, 'disabled-hold', expected=404)
        RepairPacket.objects.create(
            work_order=self.work,
            machine=self.machine,
            fault_summary='Packet-owned work',
        )
        commands = self.overview()['work_continuity']['commands']
        self.assertEqual(commands['unavailable_reason'], 'packet_owns_lifecycle')
        self.assertFalse(commands['hold'])
        self.post_command('hold', 1, 'packet-hold', expected=409)
        self.assertFalse(self.work.events.exists())

    def test_missing_or_mismatched_hold_provenance_stays_unknown(self):
        """A scalar legacy hold cannot borrow the actor/time of another reason."""
        self.work.lifecycle_status = 'on_hold'
        self.work.hold_reason = 'Current legacy hold'
        self.work.save(update_fields=['lifecycle_status', 'hold_reason'])
        event = WorkOrderEvent.objects.create(
            work_order=self.work,
            event_type='HOLD',
            to_status='on_hold',
            reason='Earlier hold',
            actor=self.user,
            correlation_id=uuid.uuid4(),
            metadata={'lifecycle_version': 1},
        )
        value = self.overview()['work_continuity']
        self.assertIsNone(value['current_hold']['recorded_by'])
        self.assertIsNone(value['current_hold']['recorded_at'])
        self.assertEqual(value['history'][0]['reason'], 'Earlier hold')
        for version in (True, '1', 0, -1, 2):
            event.reason = self.work.hold_reason
            event.metadata = {'lifecycle_version': version}
            event.save(update_fields=['reason', 'metadata'])
            self.assertIsNone(
                self.overview()['work_continuity']['current_hold']['event_id']
            )

    def test_related_work_versions_are_visible_only_inside_existing_scope(self):
        """Dependencies retain each Work's version, scope and lifecycle."""
        foreign = WorkOrder.objects.create(
            title='Foreign hidden scope',
            machine=self.machine,
            customer=Company.objects.create(
                name='Foreign continuity customer', is_customer=True
            ),
        )
        WorkOrderDependency.objects.create(
            predecessor=self.work, successor=self.sibling
        )
        WorkOrderDependency.objects.create(predecessor=self.work, successor=foreign)
        value = self.overview()
        self.assertEqual(len(value['dependencies']), 2)
        self.assertEqual(value['dependencies'][0]['card']['id'], self.sibling.pk)
        self.assertEqual(value['dependencies'][0]['card']['lifecycle_version'], 4)
        self.assertIn('Foreign hidden scope', json.dumps(value))

    def test_stale_hold_and_failed_resume_keep_current_hold_unresolved(self):
        """A version/readiness refusal cannot become a locally successful resume."""
        self.execute_permission()
        self.post_command('hold', 99, 'stale-hold', expected=409)
        self.assertEqual(self.work.events.count(), 0)
        self.post_command('hold', 1, 'valid-hold')
        WorkOrder.objects.filter(pk=self.work.pk).update(assigned_to=None)
        result = self.post_command('resume', 2, 'blocked-resume', expected=409)
        self.assertEqual(result['code'], 'ASSIGNEE_REQUIRED')
        self.work.refresh_from_db()
        self.assertEqual(
            (self.work.lifecycle_status, self.work.lifecycle_version), ('on_hold', 2)
        )
        self.assertEqual(self.work.events.count(), 1)

    def test_customer_parent_allows_projection_and_authorized_commands(self):
        """O24 shares Work continuity while command roles remain required."""
        self.execute_permission()
        self.work.customer = Company.objects.create(
            name='Out of scope continuity', is_customer=True
        )
        self.work.save(update_fields=['customer'])
        self.get(
            reverse('kanban-card-overview', kwargs={'pk': self.work.pk}),
            expected_code=200,
        )
        self.post_command('hold', 1, 'shared-hold', expected=200)
        self.assertEqual(self.work.events.count(), 1)

    def test_foreign_machine_parent_denies_projection_and_commands(self):
        """A new context projection does not broaden the existing Work boundary.

        O24 moved the foreign boundary to machine-only Work on a machine the
        reader's client scope does not hold (here a clientless machine).
        """
        self.execute_permission()
        self.work.machine = AssetMachine.objects.create(name='Unresolved continuity')
        self.work.save(update_fields=['machine'])
        self.get(
            reverse('kanban-card-overview', kwargs={'pk': self.work.pk}),
            expected_code=404,
        )
        self.post_command('hold', 1, 'foreign-hold', expected=404)
        self.assertFalse(self.work.events.exists())
