"""Occupational information and safe assignment end-to-end boundaries."""

from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse

from rest_framework.test import APIClient
from tasks.models import WorkOrder, WorkOrderEvent
from tasks.services.staff_assignment import AssignmentError, auto_assignment
from tasks.services.work_orders import assign_work_order

from assets.models import AssetMachine, Client, ClientScopeGrant
from repair.models import RepairPacket
from users.models import Trade
from users.staff_directory import directory_rows


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class StaffDirectoryTest(TestCase):
    """No directory read or assignment may escape the caller's scope."""

    @classmethod
    def setUpTestData(cls):
        """Build two clients and explicitly grant occupational capabilities."""
        cls.client_scope = Client.objects.create(code='staff-test', name='Staff test')
        cls.other_scope = Client.objects.create(code='staff-other', name='Other')
        cls.actor = User.objects.create_superuser(
            'staff-admin', 'admin@example.com', 'pw'
        )
        ClientScopeGrant.objects.create(user=cls.actor, client=cls.client_scope)
        cls.machine = AssetMachine.objects.create(
            name='Staff test pump', client=cls.client_scope
        )
        cls.plumbing, _ = Trade.objects.get_or_create(
            code='plumbing', defaults={'name': 'Plumbing'}
        )
        cls.electrical, _ = Trade.objects.get_or_create(
            code='electrical', defaults={'name': 'Electrical'}
        )
        cls.worker_group = Group.objects.create(name='Staff test technicians')
        rule = cls.worker_group.rule_sets.get(name='work_order')
        rule.can_view = True
        rule.can_execute_workorder = True
        rule.save()
        cls.plumber = cls.make_person('plumber', cls.plumbing, cls.client_scope)
        cls.electrician = cls.make_person(
            'electrician', cls.electrical, cls.client_scope
        )
        cls.outsider = cls.make_person('outsider', cls.plumbing, cls.other_scope)

    @classmethod
    def make_person(cls, username, trade, scope):
        """Create an approved scoped worker, without administrator privileges."""
        person = User.objects.create_user(username=username, password='pw')
        person.groups.add(cls.worker_group)
        person.profile.position = trade.name
        person.profile.save()
        person.profile.trades.add(trade)
        ClientScopeGrant.objects.create(user=person, client=scope)
        return person

    def setUp(self):
        """Authenticate independently for every request test."""
        self.api = APIClient()
        self.api.force_authenticate(self.actor)
        self.work = WorkOrder.objects.create(
            title='Fix pipe leak',
            machine=self.machine,
            status='backlog',
            priority='medium',
            required_trades=['plumbing'],
            trade_requirements_confirmed=True,
        )

    def test_directory_projection_is_minimal_and_scoped(self):
        """Only basic occupational data crosses a shared client boundary."""
        result = directory_rows(self.actor, machine_id=self.machine.pk)
        users = {row['user_id']: row for row in result['results']}
        self.assertIn(self.plumber.pk, users)
        self.assertNotIn(self.outsider.pk, users)
        self.assertEqual(
            set(users[self.plumber.pk]),
            {'user_id', 'display_name', 'job_title', 'trades'},
        )
        self.assertEqual(users[self.plumber.pk]['job_title'], self.plumbing.name)

    def test_unauthorized_directory_read_is_denied(self):
        """A worker's work-order role alone does not expose the directory."""
        with self.assertRaises(PermissionDenied):
            directory_rows(self.plumber, machine_id=self.machine.pk)

    def test_directory_http_contract_filters_and_bounds_results(self):
        """The real popup endpoint accepts scoped filters and minimal rows."""
        response = self.api.get(
            reverse('api-staff-directory'),
            {'machine_id': self.machine.pk, 'trade': 'plumbing', 'limit': 1},
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['user_id'], self.plumber.pk)
        self.assertEqual(
            set(response.data['results'][0]),
            {'user_id', 'display_name', 'job_title', 'trades'},
        )

    def test_directory_http_rejects_denied_and_malformed_requests(self):
        """Authorization failures and bad selectors never look like empty success."""
        url = reverse('api-staff-directory')
        self.assertEqual(
            self.api.get(url, {'work_order_id': 'invalid'}).status_code, 400
        )
        self.assertEqual(self.api.get(url, {'limit': 101}).status_code, 400)
        self.api.force_authenticate(self.electrician)
        self.assertEqual(
            self.api.get(url, {'machine_id': self.machine.pk}).status_code, 403
        )

    def test_unique_trade_match_is_not_first_available_user(self):
        """A plumber, not the electrician, receives the plumbing work."""
        result = auto_assignment(
            self.actor,
            self.work,
            required_trades=['plumbing'],
            requirements_confirmed=True,
        )
        self.assertEqual(result['assigned_to'], self.plumber.pk)
        self.assertEqual(result['code'], 'ASSIGNMENT_QUALIFIED')

    def test_ambiguous_candidates_require_review(self):
        """Multiple qualified people never yield an arbitrary first match."""
        self.make_person('second-plumber', self.plumbing, self.client_scope)
        result = auto_assignment(
            self.actor,
            self.work,
            required_trades=['plumbing'],
            requirements_confirmed=True,
        )
        self.assertIsNone(result['assigned_to'])
        self.assertEqual(result['code'], 'ASSIGNMENT_AMBIGUOUS')

    def test_unknown_requirements_require_review(self):
        """Unconfirmed work requirements cannot drive automatic assignment."""
        result = auto_assignment(
            self.actor,
            self.work,
            required_trades=['plumbing'],
            requirements_confirmed=False,
        )
        self.assertIsNone(result['assigned_to'])
        self.assertEqual(result['code'], 'ASSIGNMENT_REQUIREMENTS_UNKNOWN')

    def test_multitrade_work_needs_all_trades(self):
        """Partial trade coverage is insufficient for a combined task."""
        result = auto_assignment(
            self.actor,
            self.work,
            required_trades=['plumbing', 'electrical'],
            requirements_confirmed=True,
        )
        self.assertEqual(result['code'], 'ASSIGNMENT_NO_QUALIFIED_MATCH')
        self.plumber.profile.trades.add(self.electrical)
        result = auto_assignment(
            self.actor,
            self.work,
            required_trades=['plumbing', 'electrical'],
            requirements_confirmed=True,
        )
        self.assertEqual(result['assigned_to'], self.plumber.pk)

    def assign(self, person, **kwargs):
        """Invoke the audited command with a fresh deterministic intent."""
        return assign_work_order(
            actor=self.actor,
            work_order_id=self.work.pk,
            assigned_to=person.pk,
            expected_version=self.work.lifecycle_version,
            idempotency_key='staff-assign',
            **kwargs,
        )

    def test_trade_mismatch_does_not_write(self):
        """Rejected qualification checks leave no assignment or audit write."""
        with self.assertRaises(AssignmentError) as caught:
            self.assign(self.electrician)
        self.assertEqual(caught.exception.code, 'ASSIGNMENT_TRADE_MISMATCH')
        self.work.refresh_from_db()
        self.assertIsNone(self.work.assigned_to_id)
        self.assertFalse(self.work.events.exists())

    def test_human_override_is_reasoned_and_audited(self):
        """An explicit supervisor reason is retained in the command event."""
        result = self.assign(
            self.electrician,
            allow_override=True,
            override_reason='Supervised cross-trade task',
        )
        self.assertEqual(result.metadata['code'], 'ASSIGNMENT_MANUAL_OVERRIDE')
        event = WorkOrderEvent.objects.get(pk=result.event_id)
        self.assertEqual(
            event.metadata['result_metadata']['override_reason'],
            'Supervised cross-trade task',
        )

    def test_automation_cannot_self_override(self):
        """A model-supplied reason alone never enables an override."""
        with self.assertRaises(AssignmentError):
            self.assign(self.electrician, override_reason='Model says okay')

    def test_inactive_or_outside_person_cannot_be_overridden(self):
        """Trade overrides cannot bypass account and client boundaries."""
        for person in (self.outsider, self.plumber):
            if person == self.plumber:
                person.is_active = False
                person.save()
            with self.assertRaises(AssignmentError):
                self.assign(
                    person, allow_override=True, override_reason='Not a scope bypass'
                )

    def test_packet_backed_personnel_assignment_is_supported(self):
        """Personnel changes do not mutate the packet's lifecycle."""
        RepairPacket.objects.create(machine=self.machine, work_order=self.work)
        self.assign(self.plumber)
        self.work.refresh_from_db()
        self.assertEqual(self.work.assigned_to_id, self.plumber.pk)
        self.assertEqual(self.work.lifecycle_status, 'draft')

    def test_assignment_is_idempotent(self):
        """An exact retry emits only the original assignment event."""
        first = self.assign(self.plumber)
        replay = self.assign(self.plumber)
        self.assertEqual(first, replay)
        self.assertEqual(self.work.events.count(), 1)

    def test_legacy_user_api_does_not_bypass_title_permission(self):
        """Read-only legacy profile data cannot reveal another worker's title."""
        self.api.force_authenticate(self.electrician)
        result = self.api.get(
            reverse('api-user-detail', kwargs={'pk': self.plumber.pk})
        )
        self.assertEqual(result.status_code, 200)
        self.assertNotIn('position', result.data['profile'])
        self.assertNotIn('trades', result.data['profile'])

    def test_self_title_write_rejected_but_unrelated_edit_works(self):
        """Making title authoritative does not disable self-service status."""
        self.api.force_authenticate(self.plumber)
        url = reverse('api-user-profile')
        self.assertEqual(
            self.api.patch(url, {'position': 'Electrician'}, format='json').status_code,
            403,
        )
        self.assertEqual(
            self.api.patch(url, {'status': 'On shift'}, format='json').status_code, 200
        )

    def test_admin_occupational_endpoint_is_narrow(self):
        """Administrators edit titles/trades, not account flags through this API."""
        url = reverse('api-user-occupational-profile', kwargs={'pk': self.plumber.pk})
        result = self.api.patch(
            url,
            {
                'position': 'Pipefitter',
                'trades': [self.plumbing.pk, self.electrical.pk],
            },
            format='json',
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['position'], 'Pipefitter')
        self.assertEqual(
            set(result.data['trades']), {self.plumbing.pk, self.electrical.pk}
        )
        self.assertEqual(
            self.api.patch(url, {'is_staff': True}, format='json').status_code, 400
        )
        self.api.force_authenticate(self.plumber)
        self.assertEqual(self.api.get(url).status_code, 403)

    def test_ruleset_grants_are_real_and_independent(self):
        """The normal group editor grants directory viewing, not administration."""
        group = Group.objects.create(name='Directory viewers')
        rule = group.rule_sets.get(name='work_order')
        self.assertFalse(rule.can_view_staff_directory)
        rule.can_view_staff_directory = True
        rule.save()
        person = User.objects.create_user('directory-reader')
        person.groups.add(group)
        self.assertTrue(person.has_perm('users.view_staff_directory'))
        self.assertFalse(person.has_perm('auth.change_user'))
        self.assertFalse(person.has_perm('tasks.override_assignment_trade_mismatch'))

    def test_directory_query_count_does_not_grow_per_profile(self):
        """Prefetch profiles, trades, grants and roles once per directory page."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as first:
            directory_rows(
                User.objects.get(pk=self.actor.pk), machine_id=self.machine.pk
            )
        for i in range(4):
            self.make_person(f'query-worker-{i}', self.plumbing, self.client_scope)
        with CaptureQueriesContext(connection) as second:
            directory_rows(
                User.objects.get(pk=self.actor.pk), machine_id=self.machine.pk
            )
        self.assertLessEqual(len(second), len(first) + 1)

    def test_worker_losing_a_trade_after_preview_is_not_assigned(self):
        """A previously eligible person must still qualify at execution."""
        preview = auto_assignment(
            self.actor,
            self.work,
            required_trades=['plumbing'],
            requirements_confirmed=True,
        )
        self.assertEqual(preview['assigned_to'], self.plumber.pk)
        self.plumber.profile.trades.clear()
        with self.assertRaises(AssignmentError):
            self.assign(self.plumber)

    def test_directory_read_access_is_revocable_for_a_reused_actor(self):
        """Long-lived AI principals cannot retain a cached directory grant."""
        group = Group.objects.create(name='Temporary directory grant')
        rule = group.rule_sets.get(name='work_order')
        rule.can_view = True
        rule.can_view_staff_directory = True
        rule.save()
        self.plumber.groups.add(group)
        directory_rows(self.plumber, machine_id=self.machine.pk)
        rule.can_view_staff_directory = False
        rule.save()
        with self.assertRaises(PermissionDenied):
            directory_rows(self.plumber, machine_id=self.machine.pk)

    def test_assignment_authority_is_rechecked_for_reused_actor(self):
        """A long-lived AI actor cannot assign using a revoked cached grant."""
        group = Group.objects.create(name='Temporary assigner')
        rule = group.rule_sets.get(name='work_order')
        rule.can_view = True
        rule.can_view_staff_directory = True
        rule.can_assign_workorder = True
        rule.save()
        self.plumber.groups.add(group)
        self.assertTrue(self.plumber.has_perm('tasks.assign_workorder'))
        rule.can_assign_workorder = False
        rule.save()
        with self.assertRaises(PermissionDenied):
            assign_work_order(
                actor=self.plumber,
                work_order_id=self.work.pk,
                assigned_to=self.plumber.pk,
                expected_version=1,
                idempotency_key='revoked-assign',
            )

    def test_manual_api_creates_typed_assignment(self):
        """The maintenance workspace persists the canonical user identity."""
        response = self.api.post(
            reverse('api-staff-assignment'),
            {
                'work_order_id': self.work.pk,
                'assigned_to': self.plumber.pk,
                'expected_version': 1,
                'idempotency_key': 'manual-api',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.work.refresh_from_db()
        self.assertEqual(self.work.assigned_to_id, self.plumber.pk)

    def test_card_patch_cannot_bypass_the_assignment_command(self):
        """An older board endpoint cannot evade qualification and audit checks."""
        from tasks.serializers import KanbanCardSerializer

        card = self.work.cards.first()
        serializer = KanbanCardSerializer(
            instance=card, data={'assigned_to': self.electrician.pk}, partial=True
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('assigned_to', serializer.errors)

    def test_create_with_typed_assignment_is_atomic(self):
        """A repair intake writes identity and packet in the same aggregate."""
        from repair.work_packages import create_repair_work_package

        draft = {
            'title': 'Pipe repair',
            'machine_id': self.machine.pk,
            'planning': {
                'assigned_to': self.plumber.pk,
                'required_trades': ['plumbing'],
                'trade_requirements_confirmed': True,
            },
        }
        result = create_repair_work_package(
            actor=self.actor, draft=draft, idempotency_key='staff-create'
        )
        created = WorkOrder.objects.get(pk=result.work_order_id)
        self.assertEqual(created.assigned_to_id, self.plumber.pk)
        self.assertEqual(created.required_trades, ['plumbing'])
        self.assertTrue(hasattr(created, 'repair_packet'))

    def test_failed_creation_does_not_leave_partial_order(self):
        """A known trade mismatch cannot leave an orphaned work order."""
        from repair.work_packages import create_repair_work_package

        count = WorkOrder.objects.count()
        with self.assertRaises(AssignmentError):
            create_repair_work_package(
                actor=self.actor,
                idempotency_key='bad-create',
                draft={
                    'title': 'Pipe repair',
                    'machine_id': self.machine.pk,
                    'planning': {
                        'assigned_to': self.electrician.pk,
                        'required_trades': ['plumbing'],
                        'trade_requirements_confirmed': True,
                    },
                },
            )
        self.assertEqual(WorkOrder.objects.count(), count)

    def test_unassigned_review_outcome_survives_creation_replay(self):
        """Retries retain the reason automatic selection abstained."""
        from repair.work_packages import create_repair_work_package

        draft = {
            'title': 'Unconfirmed pipe work',
            'machine_id': self.machine.pk,
            'create_repair_packet': False,
            'planning': {'auto_assign': True, 'required_trades': ['plumbing']},
        }
        first = create_repair_work_package(
            actor=self.actor, draft=draft, idempotency_key='unknown-create'
        )
        replay = create_repair_work_package(
            actor=self.actor, draft=draft, idempotency_key='unknown-create'
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.warnings, first.warnings)
        self.assertIn('ASSIGNMENT_REQUIREMENTS_UNKNOWN', replay.warnings[0])
        self.assertIsNone(WorkOrder.objects.get(pk=first.work_order_id).assigned_to_id)

    def test_card_assignment_clear_validates_the_inherited_person(self):
        """Clearing an override cannot silently inherit a trade mismatch."""
        self.work.assigned_to = self.electrician
        self.work.save(update_fields=['assigned_to'])
        card = self.work.cards.first()
        card.assigned_to = self.plumber
        card.save(update_fields=['assigned_to'])
        with self.assertRaises(AssignmentError):
            assign_work_order(
                actor=self.actor,
                work_order_id=self.work.pk,
                kanban_card_id=card.pk,
                assigned_to=None,
                expected_version=1,
                idempotency_key='inherit-mismatch',
            )
        card.refresh_from_db()
        self.assertEqual(card.assigned_to_id, self.plumber.pk)

    def test_card_assignment_preserves_parent_handover_and_assignment_clock(self):
        """A task override is not a new work-order responsibility proposal."""
        from tasks.work_execution.time_assignment import project

        self.assign(self.plumber)
        self.work.refresh_from_db()
        before_clock = project(self.work)['assignment_event_at']
        before_handovers = self.work.handovers.count()
        card = self.work.cards.first()
        result = assign_work_order(
            actor=self.actor,
            work_order_id=self.work.pk,
            kanban_card_id=card.pk,
            assigned_to=self.plumber.pk,
            expected_version=self.work.lifecycle_version,
            idempotency_key='card-assignment-clock',
        )
        self.work.refresh_from_db()
        self.assertEqual(project(self.work)['assignment_event_at'], before_clock)
        self.assertEqual(self.work.handovers.count(), before_handovers)
        self.assertEqual(
            WorkOrderEvent.objects.get(pk=result.event_id).event_type, 'CARD_ASSIGNED'
        )
