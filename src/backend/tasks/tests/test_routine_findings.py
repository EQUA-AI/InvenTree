"""O21 explicit creation, unchanged routine records and scope-safe projections."""

from importlib import import_module
from types import SimpleNamespace
from uuid import uuid4

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from rest_framework.exceptions import ValidationError

from assets.models import AssetMachine, get_default_client
from company.models import Company
from InvenTree.unit_test import InvenTreeAPITestCase
from tasks.closeout_models import CloseoutReading
from tasks.models import (
    Procedure,
    ProcedureRevision,
    WorkOrder,
    WorkOrderCloseout,
    WorkOrderProcedureApplication,
    WorkOrderStepExecution,
)
from tasks.services.recurrence import project as recurrence
from tasks.services.routine_findings import REFUSAL, linked, origin
from tasks.services.scheduling import create_work_order, update_work_order_plan
from tasks.services.work_orders import IdempotencyConflict, StaleVersion
from users.models import RuleSet


@override_settings(
    AIMMS_WORK_ORDERS_ENABLED=True,
    AIMMS_PROCEDURES_ENABLED=True,
    AIMMS_CLOSEOUT_WIZARD_ENABLED=True,
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver',
)
class RoutineFindingTests(InvenTreeAPITestCase):
    """Exercise the existing writer and overview using actual native records."""

    roles = 'all'

    def setUp(self):
        """Create an original routine task/reading and a separate viewer."""
        super().setUp()
        self.assignRole('work_order.add')
        self.machine = AssetMachine.objects.create(
            name='O21 fixture', client=get_default_client()
        )
        self.work = WorkOrder.objects.create(
            title='Original inspection',
            machine=self.machine,
            work_order_type='inspection',
            lifecycle_status='in_progress',
            description='Original scope',
        )
        procedure = Procedure.objects.create(
            code='O21', name='Routine', created_by=self.user
        )
        revision = ProcedureRevision.objects.create(
            procedure=procedure, revision=1, created_by=self.user
        )
        self.application = WorkOrderProcedureApplication.objects.create(
            work_order=self.work,
            revision=revision,
            snapshot={},
            snapshot_hash='a' * 64,
            applied_by=self.user,
            idempotency_key='o21-application',
        )
        self.step = WorkOrderStepExecution.objects.create(
            application=self.application,
            step_key=uuid4(),
            sequence=1,
            step_snapshot={'title': 'Original task'},
            status='completed',
            value={'boolean': False},
        )
        self.reading = CloseoutReading.objects.create(
            work_order=self.work,
            label='Observed pressure',
            phase='before',
            raw_text='0 bar',
            value=0,
            unit='bar',
            recorded_by=self.user,
            step_execution=self.step,
        )
        self.viewer = get_user_model().objects.create_user(username='o21-viewer')
        group = Group.objects.create(name='O21 viewer only')
        self.viewer.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )

    def create(self, **changes):
        """Submit an explicit intent through the existing service."""
        fields = {
            'actor': self.user,
            'idempotency_key': 'o21-create',
            'title': 'Investigate observed fault',
            'machine_id': self.machine.pk,
            'recurrence_of': self.work.pk,
            'prior_version': 1,
            'corrective_origin_kind': 'routine_finding',
            'corrective_finding': 'Leak observed at inspection',
            'corrective_reading': self.reading.pk,
            'corrective_step': self.step.pk,
        }
        fields.update(changes)
        return create_work_order(**fields)

    def test_create_replay_preserves_all_original_records(self):
        """Only a new corrective Work, its event and its command are created."""
        self.work.lifecycle_status = 'completed'
        self.work.actual_completed_at = timezone.now()
        self.work.save(update_fields=['lifecycle_status', 'actual_completed_at'])
        WorkOrderCloseout.objects.create(
            work_order=self.work,
            action='Routine inspection performed',
            result='Finding retained for corrective follow-up',
            verification_summary='Original recorded check',
            completed_by=self.user,
            completed_at=self.work.actual_completed_at,
        )
        saved_outcome = (
            WorkOrderCloseout.objects.filter(work_order=self.work).values().get()
        )
        before = (
            WorkOrder.objects.filter(pk=self.work.pk).values().get(),
            list(self.work.closeout_readings.values()),
            list(self.application.step_executions.values()),
            self.work.events.count(),
        )
        result = self.create()
        self.assertEqual(result, self.create())
        corrective = WorkOrder.objects.get(pk=result.work_order_id)
        self.assertEqual(corrective.recurrence_of_id, self.work.pk)
        self.assertEqual(corrective.corrective_origin_kind, 'routine_finding')
        self.assertIsNone(corrective.recurrence_occurrence_id)
        self.assertEqual(corrective.work_order_type, 'corrective')
        self.assertEqual(corrective.corrective_reading_id, self.reading.pk)
        self.assertEqual(corrective.corrective_step_id, self.step.pk)
        self.assertEqual(corrective.events.count(), 1)
        self.assertEqual(corrective.commands.count(), 1)
        after = (
            WorkOrder.objects.filter(pk=self.work.pk).values().get(),
            list(self.work.closeout_readings.values()),
            list(self.application.step_executions.values()),
            self.work.events.count(),
        )
        self.assertEqual(before, after)
        self.assertEqual(
            saved_outcome,
            WorkOrderCloseout.objects.filter(work_order=self.work).values().get(),
        )
        self.assertEqual(len(linked(self.work, self.viewer)), 1)
        self.assertIsNone(recurrence(corrective, self.user)['linked_from'])

    def test_all_three_routine_types_and_optional_sources(self):
        """No fabricated reading or step is required for a free observation."""
        for kind in ('preventive', 'inspection', 'calibration'):
            self.work.work_order_type = kind
            self.work.save(update_fields=['work_order_type'])
            result = self.create(
                idempotency_key=kind, corrective_reading=None, corrective_step=None
            )
            self.assertEqual(
                origin(WorkOrder.objects.get(pk=result.work_order_id), self.user)['id'],
                self.work.pk,
            )

    def test_viewer_and_revoked_creator_are_refused_including_replay(self):
        """View/scope authority never grants create; replay reauthorizes."""
        with self.assertRaises(PermissionDenied):
            self.create(actor=self.viewer)
        self.create()
        with self.assertRaises(PermissionDenied):
            self.create(actor=self.viewer)
        self.user.is_active = False
        with self.assertRaises(PermissionDenied):
            self.create()

    def test_foreign_missing_wrong_machine_have_generic_refusal(self):
        """The linked-source refusal reveals no foreign Work details."""
        foreign = WorkOrder.objects.create(
            title='Secret foreign source',
            machine=self.machine,
            work_order_type='inspection',
            customer=Company.objects.create(name='Foreign O21', is_customer=True),
        )
        other = AssetMachine.objects.create(
            name='Other fixture', client=get_default_client()
        )
        for changes in (
            {'recurrence_of': foreign.pk},
            {'recurrence_of': 987654321},
            {'machine_id': other.pk},
        ):
            with self.assertRaises(ValidationError) as caught:
                self.create(**changes)
            self.assertEqual(caught.exception.detail, [REFUSAL])

    def test_http_foreign_source_and_missing_source_share_generic_refusal(self):
        """A permitted creator cannot probe another Work boundary over HTTP."""
        foreign = WorkOrder.objects.create(
            title='Secret source',
            machine=self.machine,
            work_order_type='inspection',
            customer=Company.objects.create(name='Private boundary', is_customer=True),
        )
        responses = []
        for identity in (foreign.pk, 987654321):
            result = self.post(
                '/api/kanban/work-orders/commands/create/',
                {
                    'machine': self.machine.pk,
                    'title': 'Corrective',
                    'recurrence_of': identity,
                    'prior_version': 1,
                    'corrective_origin_kind': 'routine_finding',
                    'corrective_finding': 'Observed drip',
                    'idempotency_key': f'foreign-{identity}',
                },
                expected_code=400,
            )
            responses.append(result.data)
        self.assertEqual(responses[0], responses[1])
        self.assertEqual(responses[0], [REFUSAL])

    def test_wrong_intent_and_foreign_reading_step_refused(self):
        """Do not mix recurrence, routine or sibling evidence namespaces."""
        other = WorkOrder.objects.create(title='Sibling', machine=self.machine)
        reading = CloseoutReading.objects.create(
            work_order=other,
            label='Other',
            phase='before',
            raw_text='1',
            recorded_by=self.user,
        )
        cases = [
            {'work_order_type': 'preventive'},
            {'recurrence_occurrence': 123},
            {'corrective_finding': '  '},
            {'corrective_reading': reading.pk},
            {'corrective_step': 987654321},
            {'recurrence_of': True},
            {'prior_version': True},
        ]
        for case in cases:
            with self.assertRaises(ValidationError):
                self.create(**case)
        self.work.work_order_type = 'corrective'
        self.work.save(update_fields=['work_order_type'])
        with self.assertRaises(ValidationError):
            self.create()
        self.assertEqual(
            WorkOrder.objects.filter(corrective_origin_kind='routine_finding').count(),
            0,
        )

    def test_stale_and_different_intent_cannot_create_duplicate(self):
        """Exact replay precedes version, but altered text cannot reuse the key."""
        with self.assertRaises(StaleVersion):
            self.create(prior_version=2)
        result = self.create()
        self.work.lifecycle_version = 2
        self.work.save(update_fields=['lifecycle_version'])
        self.assertEqual(self.create(), result)
        with self.assertRaises(IdempotencyConflict):
            self.create(corrective_finding='Different observation')

    def test_projection_hides_unreadable_links_in_both_directions(self):
        """No hidden child identity/count or unreadable source finding escapes."""
        child = WorkOrder.objects.get(pk=self.create().work_order_id)
        child.customer = Company.objects.create(name='Private child', is_customer=True)
        child.save(update_fields=['customer'])
        self.assertEqual(linked(self.work, self.viewer), [])
        child.customer = None
        child.save(update_fields=['customer'])
        self.work.customer = Company.objects.create(
            name='Private original', is_customer=True
        )
        self.work.save(update_fields=['customer'])
        self.assertEqual(origin(child, self.viewer), {'state': 'unavailable'})
        self.assertIsNone(origin(self.work, self.viewer))
        self.assertEqual(linked(self.work, None), [])

    def test_http_hides_unreadable_links_and_event_source_identities(self):
        """Overview and history metadata cannot bypass related-Work scope."""
        child = WorkOrder.objects.get(pk=self.create().work_order_id)
        child.customer = Company.objects.create(
            name='Private child HTTP', is_customer=True
        )
        child.save(update_fields=['customer'])
        result = self.get(
            f'/api/kanban/work-orders/{self.work.pk}/overview/', expected_code=200
        )
        self.assertEqual(result.data['routine_work']['linked_corrective'], [])
        child.customer = None
        child.save(update_fields=['customer'])
        self.work.customer = Company.objects.create(
            name='Private source HTTP', is_customer=True
        )
        self.work.save(update_fields=['customer'])
        result = self.get(
            f'/api/kanban/work-orders/{child.pk}/overview/', expected_code=200
        )
        self.assertEqual(result.data['routine_origin'], {'state': 'unavailable'})
        self.assertNotIn(
            'routine_work_id', result.data['events'][0]['metadata']['result_metadata']
        )
        self.assertEqual(
            result.data['events'][0]['metadata']['result_metadata']['source_state'],
            'unavailable',
        )

    def test_exact_replay_survives_later_source_type_change(self):
        """Historical origin survives a later plan edit without a second create."""
        result = self.create()
        self.work.work_order_type = 'other'
        self.work.lifecycle_version = 2
        self.work.save(update_fields=['work_order_type', 'lifecycle_version'])
        self.assertEqual(self.create(), result)
        with self.assertRaises(ValidationError):
            self.create(idempotency_key='another', prior_version=2)

    def test_http_receipt_overviews_and_read_only_load(self):
        """The real endpoint returns the durable command and both scoped projections."""
        url = '/api/kanban/work-orders/commands/create/'
        body = {
            'title': 'Corrective from HTTP',
            'machine': self.machine.pk,
            'recurrence_of': self.work.pk,
            'prior_version': 1,
            'corrective_origin_kind': 'routine_finding',
            'corrective_finding': 'Observed fault',
            'idempotency_key': 'http-o21',
        }
        response = self.post(url, body, expected_code=201)
        receipt = response.data['process_receipt']
        self.assertEqual(receipt['metadata']['routine_work_id'], self.work.pk)
        self.assertEqual(receipt['metadata']['origin_kind'], 'routine_finding')
        self.assertEqual(
            self.post(url, body, expected_code=201).data['process_receipt'], receipt
        )
        for _ in range(2):
            original = self.get(
                f'/api/kanban/work-orders/{self.work.pk}/overview/', expected_code=200
            )
            self.assertEqual(
                original.data['routine_work']['linked_corrective'][0]['id'],
                response.data['id'],
            )
            self.assertTrue(original.data['routine_work']['can_create_linked'])
            corrective = self.get(
                f'/api/kanban/work-orders/{response.data["id"]}/overview/',
                expected_code=200,
            )
            self.assertEqual(
                corrective.data['routine_origin']['reference'], self.work.reference
            )
            self.assertEqual(
                corrective.data['events'][0]['metadata']['result_metadata'][
                    'routine_work_id'
                ],
                self.work.pk,
            )
            self.assertNotIn(
                'source_state',
                corrective.data['events'][0]['metadata']['result_metadata'],
            )
        self.assertEqual(self.work.events.count(), 0)
        self.client.force_authenticate(user=self.viewer)
        self.assertEqual(self.client.post(url, body, format='json').status_code, 403)
        self.assertEqual(
            WorkOrder.objects.filter(corrective_origin_kind='routine_finding').count(),
            1,
        )

    def test_review_replay_after_machine_move_returns_original_http_receipt(self):
        """A later authorized plan move cannot strand a committed create intent."""
        body = {
            'title': 'Corrective before routine move',
            'machine': self.machine.pk,
            'recurrence_of': self.work.pk,
            'prior_version': 1,
            'corrective_origin_kind': 'routine_finding',
            'corrective_finding': 'Observed drip',
            'idempotency_key': 'review-move-http',
        }
        url = '/api/kanban/work-orders/commands/create/'
        first = self.post(url, body, expected_code=201)
        receipt = first.data['process_receipt']
        other = AssetMachine.objects.create(
            name='New routine machine', client=get_default_client()
        )
        update_work_order_plan(
            work_order_id=self.work.pk,
            actor=self.user,
            expected_version=1,
            idempotency_key='review-move',
            fields={'machine_id': other.pk},
        )
        replay = self.post(url, body, expected_code=201)
        self.assertEqual(replay.data['process_receipt'], receipt)
        self.assertEqual(replay.data['id'], first.data['id'])
        child = WorkOrder.objects.get(pk=first.data['id'])
        self.assertEqual(child.machine_id, self.machine.pk)
        self.assertEqual(child.commands.count(), 1)
        self.assertEqual(WorkOrder.objects.filter(recurrence_of=self.work).count(), 1)
        fresh = {**body, 'idempotency_key': 'review-move-new', 'prior_version': 2}
        self.post(url, fresh, expected_code=400)
        self.work.refresh_from_db()
        self.assertEqual(
            (self.work.machine_id, self.work.lifecycle_version), (other.pk, 2)
        )

    def test_review_moved_source_replay_still_requires_both_work_scopes(self):
        """Replay authorizes the stored corrective and the current source separately."""
        result = self.create()
        child = WorkOrder.objects.get(pk=result.work_order_id)
        other = AssetMachine.objects.create(
            name='Scope move fixture', client=get_default_client()
        )
        update_work_order_plan(
            work_order_id=self.work.pk,
            actor=self.user,
            expected_version=1,
            idempotency_key='review-scope-move',
            fields={'machine_id': other.pk},
        )
        self.assertEqual(self.create(), result)
        foreign = Company.objects.create(
            name='Review private boundary', is_customer=True
        )
        for work in (self.work, child):
            WorkOrder.objects.filter(pk=work.pk).update(customer=foreign)
            with self.assertRaises(ValidationError) as caught:
                self.create()
            self.assertEqual(caught.exception.detail, [REFUSAL])
            WorkOrder.objects.filter(pk=work.pk).update(customer=None)
        self.assertEqual(self.create(), result)
        self.assertEqual(child.commands.count(), 1)

    def test_review_unavailable_creation_hints_name_the_actual_reason(self):
        """Authorization, lifecycle, absent machine scope and unknown hints stay distinct."""
        from tasks.work_execution.routine import project

        self.assertEqual(
            project(self.work, self.user).get('create_unavailable_reason'), None
        )
        viewer = project(self.work, self.viewer)
        self.assertFalse(viewer['can_create_linked'])
        self.assertEqual(viewer.get('create_unavailable_reason'), 'permission')
        for fields in ({'is_active': False}, {'lifecycle_status': 'canceled'}):
            for field, value in fields.items():
                setattr(self.work, field, value)
            value = project(self.work, self.user)
            self.assertFalse(value['can_create_linked'])
            self.assertEqual(
                value.get('create_unavailable_reason'), 'routine_inactive_or_canceled'
            )
            self.work.is_active = True
            self.work.lifecycle_status = 'in_progress'
        self.work.customer = Company.objects.create(
            name='Review readable claim', is_customer=True
        )
        self.work.save(update_fields=['customer'])
        self.machine.client = None
        self.machine.save(update_fields=['client'])
        from tasks.scope import MaintenanceScope

        with override_settings(
            AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: [
                MaintenanceScope(customer_id=self.work.customer_id, site_key=None)
            ]
        ):
            self.assertEqual(
                project(self.work, self.user).get('create_unavailable_reason'),
                'machine_scope',
            )

    def test_review_reading_step_pair_must_match_even_with_valid_local_ids(self):
        """Two individually valid routine records cannot be linked as a false pair."""
        step = WorkOrderStepExecution.objects.create(
            application=self.application,
            step_key=uuid4(),
            sequence=2,
            step_snapshot={'title': 'Other step'},
        )
        independent = CloseoutReading.objects.create(
            work_order=self.work,
            label='Independent observation',
            raw_text='Observed',
            recorded_by=self.user,
        )
        for fields in (
            {'corrective_step': step.pk},
            {'corrective_reading': independent.pk},
        ):
            with self.assertRaises(ValidationError) as caught:
                self.create(**fields)
            self.assertEqual(caught.exception.detail, [REFUSAL])
        self.assertEqual(WorkOrder.objects.filter(recurrence_of=self.work).count(), 0)

    def test_review_0038_backfill_marks_only_existing_linked_recurrences(self):
        """The local migration distinguishes linked legacy rows from ordinary Work."""
        legacy = WorkOrder.objects.create(
            title='Legacy linked recurrence',
            machine=self.machine,
            recurrence_of=self.work,
            corrective_origin_kind='',
        )
        ordinary = WorkOrder.objects.create(
            title='Ordinary unlinked Work', machine=self.machine
        )
        migration = import_module('tasks.migrations.0038_routine_corrective_origin')
        migration.mark_recurrences(apps, SimpleNamespace(connection=connection))
        legacy.refresh_from_db()
        ordinary.refresh_from_db()
        self.work.refresh_from_db()
        self.assertEqual(legacy.corrective_origin_kind, 'recurrence')
        self.assertEqual(ordinary.corrective_origin_kind, '')
        self.assertEqual(self.work.corrective_origin_kind, '')
