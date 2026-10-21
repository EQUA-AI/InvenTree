"""Legacy maintenance HTTP access follows the canonical object boundary."""

import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from rest_framework.test import APIClient

from assets.models import AssetMachine, Client, ClientScopeGrant
from common.models import Attachment
from company.models import Company
from part.models import Part
from repair.models import RepairPacket, RepairPacketGate
from tasks.models import WorkOrder, WorkOrderCommand, WorkOrderDependency, WorkOrderPart
from users.models import RuleSet


@override_settings(
    AIMMS_WORK_ORDERS_ENABLED=True,
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
)
class LegacyWorkScopeAPITest(TestCase):
    """Authenticate through a real test session with stored roles and grants."""

    def setUp(self):
        """Create synthetic own, foreign, customer-owned and unresolved work."""
        self.actor = get_user_model().objects.create_user(
            username='legacy-scope-operator', password='synthetic-only'
        )
        group = Group.objects.create(name='Legacy scoped operators')
        self.rules, _created = RuleSet.objects.update_or_create(
            name='work_order',
            group=group,
            defaults={
                'can_view': True,
                'can_add': True,
                'can_change': True,
                'can_delete': True,
            },
        )
        self.actor.groups.add(group)
        self.tenant = Client.objects.create(name='Scope A', code='legacy-scope-a')
        foreign_tenant = Client.objects.create(name='Scope B', code='legacy-scope-b')
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.tenant
        )
        self.machine = AssetMachine.objects.create(
            name='Scope A pump', client=self.tenant
        )
        self.foreign_machine = AssetMachine.objects.create(
            name='Scope B pump', client=foreign_tenant
        )
        self.own = WorkOrder.objects.create(title='Owned work', machine=self.machine)
        self.foreign = WorkOrder.objects.create(
            title='Foreign work', machine=self.foreign_machine
        )
        customer = Company.objects.create(name='Sales customer', is_customer=True)
        self.customer_owned = WorkOrder.objects.create(
            title='Shared customer Work', machine=self.machine, customer=customer
        )
        self.orphan = WorkOrder.objects.create(title='Unresolved work')
        part = Part.objects.create(name='Synthetic scope part')
        self.foreign_part = WorkOrderPart.objects.create(
            work_order=self.foreign, part=part, quantity=1
        )
        self.api = APIClient()
        self.assertTrue(
            self.api.login(username=self.actor.username, password='synthetic-only')
        )

    def resource(self, work_order, suffix=''):
        """Return one legacy work resource URL."""
        return f'/api/kanban/work-orders/{work_order.pk}/{suffix}'

    def step(self, work_order):
        """Create a saved Work-owned step without granting procedure catalog access."""
        from tasks.procedure_models import (
            Procedure,
            ProcedureRevision,
            WorkOrderProcedureApplication,
            WorkOrderStepExecution,
        )

        procedure = Procedure.objects.create(
            code=f'O24-STEP-{work_order.pk}',
            name='Saved procedure',
            created_by=self.actor,
        )
        revision = ProcedureRevision.objects.create(
            procedure=procedure,
            revision=1,
            work_order_type='corrective',
            created_by=self.actor,
        )
        application = WorkOrderProcedureApplication.objects.create(
            work_order=work_order,
            revision=revision,
            snapshot={},
            snapshot_hash='0' * 64,
            applied_by=self.actor,
            idempotency_key='o24-step-attachment',
        )
        return WorkOrderStepExecution.objects.create(
            application=application, step_key=uuid.uuid4(), sequence=1, step_snapshot={}
        )

    def test_collections_include_only_the_canonical_boundary(self):
        """Shared Work is included; foreign machine-only Work remains withheld."""
        for endpoint in ('/api/kanban/work-orders/', '/api/tasks/work-orders/'):
            with self.subTest(endpoint=endpoint):
                response = self.api.get(endpoint)
                self.assertEqual(response.status_code, 200)
                items = (
                    response.data['results']
                    if isinstance(response.data, dict)
                    else response.data
                )
                self.assertEqual(
                    {item['id'] for item in items},
                    {self.own.pk, self.customer_owned.pk, self.orphan.pk},
                )
        cards = self.api.get('/api/kanban/cards/')
        self.assertEqual(cards.status_code, 200)
        self.assertEqual(
            {item['work_order'] for item in cards.data},
            {self.own.pk, self.customer_owned.pk, self.orphan.pk},
        )

    def test_detail_overview_card_and_part_fail_closed(self):
        """Forbidden and unresolved records return scope-safe not-found results."""
        for work_order in (self.foreign,):
            for endpoint in (
                self.resource(work_order),
                self.resource(work_order, 'overview/'),
                self.resource(work_order, 'parts/'),
                f'/api/kanban/cards/{work_order.primary_card.pk}/',
            ):
                with self.subTest(endpoint=endpoint):
                    self.assertEqual(self.api.get(endpoint).status_code, 404)
        self.assertEqual(
            self.api.get(
                self.resource(self.foreign, f'parts/{self.foreign_part.pk}/')
            ).status_code,
            404,
        )
        self.assertEqual(
            self.api.get(self.resource(self.own, 'overview/')).status_code, 200
        )

    def test_shared_work_detail_and_edits_keep_foreign_machine_scope(self):
        """A normal client actor reads/edits shared Work, never its foreign asset."""
        # Registered pump endpoints use client scope; generic equipment has
        # a separate retained legacy visibility contract.
        self.foreign_machine.parent = AssetMachine.objects.create(
            name='O24 foreign station', asset_type='pumphouse',
            client=self.foreign_machine.client, source_namespace='o24',
            source_entity_uuid=uuid.uuid4(),
        )
        self.foreign_machine.asset_type = 'pump'
        self.foreign_machine.source_key = 'o24-foreign-pump'  # gitleaks:allow
        self.foreign_machine.save(update_fields=['asset_type', 'parent', 'source_key'])
        self.customer_owned.machine = self.foreign_machine
        self.customer_owned.save(update_fields=['machine'])
        for work in (self.customer_owned, self.orphan):
            with self.subTest(work=work.pk):
                self.assertEqual(self.api.get(self.resource(work)).status_code, 200)
                response = self.api.patch(
                    self.resource(work), {'title': 'Shared edit'}, format='json'
                )
                self.assertEqual(response.status_code, 200, response.data)
                work.refresh_from_db()
                self.assertEqual(work.title, 'Shared edit')
        packet = RepairPacket.objects.create(
            work_order=self.customer_owned, machine=self.foreign_machine
        )
        for path in (
            f'/api/assets/machines/{self.foreign_machine.pk}/',
            f'/api/machine-health/machines/{self.foreign_machine.pk}/health/',
            f'/api/machine-health/machines/{self.foreign_machine.pk}/health/signals/',
            f'/api/repair/packets/{packet.pk}/',
        ):
            with self.subTest(path=path):
                self.assertEqual(self.api.get(path).status_code, 404)
        # P32: a shared customer does not authorize newly attaching a machine.
        for path in (self.resource(self.own), self.resource(self.orphan)):
            denied = self.api.patch(
                path,
                {
                    'customer': self.customer_owned.customer_id,
                    'machine': self.foreign_machine.pk,
                },
                format='json',
            )
            self.assertEqual(denied.status_code, 404)

    def _foreign_machine_records(self):
        """Put shared customer Work on the foreign machine with its records."""
        import datetime

        from django.utils import timezone

        from assets.health_models import MachineAnomaly
        from assets.models import AssetMaintenanceRecord

        self.customer_owned.machine = self.foreign_machine
        self.customer_owned.save(update_fields=['machine'])
        packet = RepairPacket.objects.create(
            work_order=self.customer_owned,
            machine=self.foreign_machine,
            fault_summary='O24 foreign packet secret',
        )
        RepairPacketGate.objects.create(
            packet=packet, name='O24 foreign isolation gate', gate_type='loto'
        )
        now = timezone.now()
        MachineAnomaly.objects.create(
            machine=self.foreign_machine,
            work_order=self.customer_owned,
            fingerprint='o24-foreign-alert',
            title='O24 foreign alert secret',
            first_observed_at=now,
            last_observed_at=now,
        )
        AssetMaintenanceRecord.objects.create(
            machine=self.foreign_machine,
            work_order=self.customer_owned,
            date=datetime.date(2026, 10, 1),
            summary='O24 foreign history secret',
        )
        return packet

    def test_shared_work_overview_withholds_foreign_machine_records(self):
        """O24: Work fields are shared; packet, alert and history stay machine-scoped."""
        packet = self._foreign_machine_records()
        response = self.api.get(self.resource(self.customer_owned, 'overview/'))
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertEqual(data['title'], 'Shared customer Work')
        self.assertIsNone(data['repair_packet'])
        self.assertIsNone(data['source_alert'])
        self.assertIsNone(data['maintenance_record'])
        self.assertIsNone(data['investigation']['packet'])
        self.assertEqual(data['operational_authority']['gates'], [])
        self.assertEqual(data['operational_authority']['telemetry'], [])
        body = str(data)
        for secret in (
            'O24 foreign packet secret',
            'O24 foreign isolation gate',
            'O24 foreign alert secret',
            'O24 foreign history secret',
        ):
            self.assertNotIn(secret, body)
        # The same Work on the reader's own machine projects every section.
        self.customer_owned.machine = self.machine
        self.customer_owned.save(update_fields=['machine'])
        # Persisted mismatches must not bypass the packet/record machine binding.
        mismatched = self.api.get(self.resource(self.customer_owned, 'overview/')).data
        self.assertIsNone(mismatched['repair_packet'])
        self.assertIsNone(mismatched['investigation']['packet'])
        self.assertIsNone(mismatched['source_alert'])
        self.assertIsNone(mismatched['maintenance_record'])
        RepairPacket.objects.filter(pk=packet.pk).update(machine=self.machine)
        self.customer_owned.anomalies.update(machine=self.machine)
        record = self.customer_owned.maintenance_record
        record.machine = self.machine
        record.save(update_fields=['machine'])
        own = self.api.get(self.resource(self.customer_owned, 'overview/')).data
        self.assertEqual(own['repair_packet']['id'], packet.pk)
        self.assertEqual(own['source_alert']['title'], 'O24 foreign alert secret')
        self.assertEqual(
            own['maintenance_record']['summary'], 'O24 foreign history secret'
        )
        self.assertEqual(
            [gate['name'] for gate in own['operational_authority']['gates']],
            ['O24 foreign isolation gate'],
        )

    @override_settings(AIMMS_MAINTENANCE_AI_READ_ENABLED=True)
    def test_shared_work_ai_repair_state_withholds_foreign_packet(self):
        """The AI Work tool follows the overview: no foreign packet through Work."""
        from asgiref.sync import async_to_sync

        from ai.core.auth import AIPrincipal, principal_context
        from ai.core.tools.inventree.read.maintenance import get_work_order_repair_state
        from tasks.scope import work_machine_authorized

        packet = self._foreign_machine_records()
        principal = AIPrincipal(
            subject=f'user:{self.actor.pk}',
            actor=f'user:{self.actor.pk}',
            user_pk=str(self.actor.pk),
            username=self.actor.get_username(),
            authentication_method='session',
            scope='chat',
            policy_version='test',
            is_staff=False,
            is_superuser=False,
        )

        def call():
            token = principal_context.set(principal)
            try:
                return async_to_sync(get_work_order_repair_state)(
                    work_order_id=self.customer_owned.pk
                )
            finally:
                principal_context.reset(token)

        self.assertFalse(work_machine_authorized(self.actor, self.customer_owned))
        self.assertFalse(work_machine_authorized(self.actor, self.orphan))
        result = call()
        self.assertEqual(result['work_order_id'], self.customer_owned.pk)
        self.assertIsNone(result['packet'])
        self.assertNotIn('O24 foreign packet secret', str(result))
        self.customer_owned.machine = self.machine
        self.customer_owned.save(update_fields=['machine'])
        self.assertIsNone(call()['packet'])
        RepairPacket.objects.filter(pk=packet.pk).update(machine=self.machine)
        self.assertEqual(call()['packet']['packet_id'], packet.pk)

    def test_safety_gate_approval_needs_the_packet_machine_scope(self):
        """Shared customer Work cannot authorize a gate on a foreign machine."""
        from types import SimpleNamespace

        from approvals.executors import SafetyGateExecutor
        from tasks.scope import ScopeError

        packet = self._foreign_machine_records()
        gate = packet.gates.get()
        with self.assertRaises(ScopeError):
            SafetyGateExecutor().execute_for_approval(
                SimpleNamespace(payload={'gate_id': gate.pk}),
                actor=self.actor,
                execution=None,
            )

    def test_view_only_viewer_reads_but_cannot_change_shared_work(self):
        """Work view shares customer/unattributed Work; edits still need change."""
        self.rules.can_add = False
        self.rules.can_change = False
        self.rules.can_delete = False
        self.rules.save()
        for work in (self.customer_owned, self.orphan):
            with self.subTest(work=work.pk):
                self.assertEqual(self.api.get(self.resource(work)).status_code, 200)
                self.assertEqual(
                    self.api.patch(
                        self.resource(work), {'title': 'No'}, format='json'
                    ).status_code,
                    403,
                )
                self.assertEqual(self.api.delete(self.resource(work)).status_code, 403)
                work.refresh_from_db()
                self.assertNotEqual(work.title, 'No')

    def test_customer_create_requires_role_and_authorized_machine(self):
        """Customer selection is shared; proposed machine authorization stays exact."""
        before = WorkOrder.objects.count()
        for machine, expected in ((self.machine, 201), (self.foreign_machine, 404)):
            result = self.api.post(
                '/api/kanban/work-orders/',
                {
                    'title': 'O24 customer create',
                    'customer': self.customer_owned.customer_id,
                    'machine': machine.pk,
                    'status': 'backlog',
                    'priority': 'medium',
                },
                format='json',
            )
            self.assertEqual(result.status_code, expected, result.data)
        self.assertEqual(WorkOrder.objects.count(), before + 1)

    def test_board_schedule_dependencies_and_ai_include_shared_work(self):
        """All canonical read paths include customer and unattributed Work."""
        from tasks import ai_read
        from tasks.models import WorkOrderDependency

        WorkOrderDependency.objects.create(
            predecessor=self.customer_owned, successor=self.orphan
        )
        expected = {self.own.pk, self.customer_owned.pk, self.orphan.pk}
        board = self.api.get('/api/kanban/cards/')
        self.assertEqual({row['work_order'] for row in board.data}, expected)
        window = self.api.get('/api/kanban/schedule/')
        self.assertEqual({row['id'] for row in window.data['cards']}, expected)
        self.assertEqual(len(window.data['dependencies']), 1)
        overview = self.api.get(self.resource(self.orphan, 'overview/'))
        self.assertEqual(
            overview.data['dependencies'][0]['card']['id'], self.customer_owned.pk
        )
        with override_settings(AIMMS_MAINTENANCE_AI_READ_ENABLED=True):
            self.assertEqual(
                {work.pk for work in ai_read.work_orders_in_scope(self.actor)}, expected
            )
            self.assertEqual(
                ai_read.authorized_work_order(self.actor, self.customer_owned.pk).pk,
                self.customer_owned.pk,
            )

    def test_shared_work_is_not_disclosed_over_http_without_view_role(self):
        """Even an independent resolved client scope cannot replace Work view."""
        from tasks.scope import MaintenanceScope

        self.actor.groups.clear()
        scope = MaintenanceScope(customer_id=None, site_key=None, client_id=self.tenant.pk)
        with override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: {scope}):
            for work in (self.customer_owned, self.orphan):
                for path in (self.resource(work), self.resource(work, 'overview/'), f'/api/tasks/work-orders/{work.pk}/'):
                    with self.subTest(path=path):
                        result = self.api.get(path)
                        self.assertIn(result.status_code, (403, 404))
                        self.assertNotIn(work.title, str(result.data))

    def test_shared_work_still_requires_active_viewer(self):
        """Independent resolver grants cannot substitute for view or active state."""
        from tasks.scope import (
            MaintenanceScope,
            ScopeError,
            require_work_order_scope,
            scoped_work_orders,
        )

        scopes = {MaintenanceScope(customer_id=None, site_key=None, client_id=self.tenant.pk)}
        roleless = type(self.actor).objects.create_user(username='o24-roleless')
        with override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: scopes):
            self.actor.is_active = False
            for actor in (roleless, self.actor):
                for work in (self.customer_owned, self.orphan):
                    with self.subTest(actor=actor.pk, work=work.pk), self.assertRaises(ScopeError):
                        require_work_order_scope(actor, work)
                self.assertFalse(scoped_work_orders(actor).exists())
            self.actor.is_active = True
        with override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: []):
            self.assertEqual(
                set(scoped_work_orders(self.actor).values_list('pk', flat=True)),
                {self.customer_owned.pk, self.orphan.pk},
            )

    @override_settings(AIMMS_SINGLE_SITE_CLIENT_CODE='o24-no-fallback-client')
    def test_viewer_without_any_client_grant_reads_shared_work_and_attachments(self):
        """Production resolver may return nothing; shared Work needs view alone."""
        from tasks.scope import ScopeError, scope_for_actor

        self.grant.delete()
        with self.assertRaises(ScopeError):
            scope_for_actor(self.actor)
        self.rules.can_add = False
        self.rules.can_change = False
        self.rules.can_delete = False
        self.rules.save()
        expected = {self.customer_owned.pk, self.orphan.pk}
        for endpoint in ('/api/kanban/work-orders/', '/api/tasks/work-orders/'):
            response = self.api.get(endpoint)
            self.assertEqual(response.status_code, 200, response.data)
            rows = response.data.get('results', []) if isinstance(response.data, dict) else response.data
            self.assertEqual({row['id'] for row in rows}, expected)
        for work in (self.customer_owned, self.orphan):
            attachment = Attachment.objects.create(
                model_type='workorder', model_id=work.pk, comment='O24 shared evidence',
                link='https://example.invalid/o24-shared-evidence',
            )
            for endpoint in (
                self.resource(work),
                self.resource(work, 'overview/'),
                f'/api/tasks/work-orders/{work.pk}/',
                f'/api/attachment/{attachment.pk}/',
            ):
                with self.subTest(endpoint=endpoint):
                    response = self.api.get(endpoint)
                    self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(self.api.patch(self.resource(work), {'title': 'Forbidden'}, format='json').status_code, 403)
        for work in (self.own, self.foreign):
            self.assertEqual(self.api.get(self.resource(work)).status_code, 404)
        # The same no-grant actor may edit shared Work once change is granted.
        self.rules.can_change = True
        self.rules.save()
        self.assertEqual(self.api.patch(self.resource(self.orphan), {'title': 'Allowed'}, format='json').status_code, 200)
        self.assertEqual(self.api.patch(self.resource(self.orphan), {'machine': self.machine.pk}, format='json').status_code, 404)

    def test_revocation_removes_machine_only_work_but_retains_shared_work(self):
        """Single-site fallback retains shared Work, never the revoked client machine."""
        self.assertEqual(self.api.get(self.resource(self.own)).status_code, 200)
        self.grant.delete()
        shared = {self.customer_owned.pk, self.orphan.pk}
        self.assertEqual({row['id'] for row in self.api.get('/api/kanban/work-orders/').data}, shared)
        self.assertEqual({row['work_order'] for row in self.api.get('/api/kanban/cards/').data}, shared)
        self.assertEqual(
            self.api.get(self.resource(self.own, 'overview/')).status_code, 404
        )

    def test_feature_flag_off_does_not_disable_legacy_scope(self):
        """Disabling additive commands does not create global read access."""
        with override_settings(AIMMS_WORK_ORDERS_ENABLED=False):
            self.assertEqual(
                self.api.get(self.resource(self.own, 'overview/')).status_code, 200
            )
            self.assertEqual(
                self.api.get(self.resource(self.foreign, 'overview/')).status_code, 404
            )

    def test_legacy_edits_cannot_cross_or_reassign_a_boundary(self):
        """Check both the original object and its proposed owning machine."""
        outside = self.api.patch(
            self.resource(self.foreign), {'title': 'No'}, format='json'
        )
        self.assertEqual(outside.status_code, 404)
        moved = self.api.patch(
            self.resource(self.own), {'machine': self.foreign_machine.pk}, format='json'
        )
        self.assertEqual(moved.status_code, 404)
        self.own.refresh_from_db()
        self.foreign.refresh_from_db()
        self.assertEqual(self.own.machine_id, self.machine.pk)
        self.assertEqual(self.foreign.title, 'Foreign work')
        allowed = self.api.patch(
            self.resource(self.own), {'title': 'Allowed'}, format='json'
        )
        self.assertEqual(allowed.status_code, 200)

    def test_creating_work_or_a_card_requires_the_owner_scope(self):
        """An add role is not authority to select another client's parent."""
        work = self.api.post(
            '/api/kanban/work-orders/',
            {
                'title': 'Denied create',
                'machine': self.foreign_machine.pk,
                'status': 'backlog',
                'priority': 'medium',
            },
            format='json',
        )
        self.assertEqual(work.status_code, 404, work.data)
        card = self.api.post(
            '/api/kanban/cards/',
            {
                'title': 'Denied card',
                'work_order': self.foreign.pk,
                'status': 'backlog',
                'card_kind': 'subtask',
            },
            format='json',
        )
        self.assertEqual(card.status_code, 404)
        self.assertFalse(WorkOrder.objects.filter(title='Denied create').exists())

    def test_overview_and_schedule_do_not_enrich_foreign_dependencies(self):
        """Nested context and window edges cannot leak an inaccessible job."""
        WorkOrderDependency.objects.create(predecessor=self.foreign, successor=self.own)
        overview = self.api.get(self.resource(self.own, 'overview/'))
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.data['dependencies'], [])
        window = self.api.get('/api/kanban/schedule/')
        self.assertEqual(window.status_code, 200)
        self.assertEqual(
            {item['id'] for item in window.data['cards']},
            {self.own.pk, self.customer_owned.pk, self.orphan.pk},
        )
        self.assertEqual(window.data['dependencies'], [])

    def test_view_only_scope_does_not_grant_mutation_roles(self):
        """Preserve the independent role gate for in-scope records."""
        self.rules.can_add = False
        self.rules.can_change = False
        self.rules.can_delete = False
        self.rules.save()
        self.assertEqual(self.api.get(self.resource(self.own)).status_code, 200)
        self.assertEqual(
            self.api.patch(
                self.resource(self.own), {'title': 'No'}, format='json'
            ).status_code,
            403,
        )
        self.assertEqual(self.api.delete(self.resource(self.own)).status_code, 403)

    def test_foreign_commands_and_mixed_batches_never_mutate(self):
        """Every legacy HTTP planning command authorizes its parent before work."""
        for command in (
            'update',
            'schedule',
            'resize',
            'delete',
            'create-child',
            'generate-procurement',
        ):
            response = self.api.post(
                self.resource(self.foreign, f'commands/{command}/'), {}, format='json'
            )
            self.assertEqual(response.status_code, 404, (command, response.data))
        for endpoint, payload in (
            (
                '/api/kanban/schedule/apply/',
                {
                    'operations': [
                        {'card_id': self.own.pk},
                        {'card_id': self.foreign.pk},
                    ]
                },
            ),
            (
                '/api/kanban/schedule/plan/',
                {'candidate_ids': [self.own.pk, self.foreign.pk]},
            ),
            ('/api/kanban/schedule/optimize/', {'candidate_ids': [self.foreign.pk]}),
            (
                '/api/kanban/dependencies/',
                {'predecessor': self.own.pk, 'successor': self.foreign.pk},
            ),
            (
                '/api/kanban/work-orders/commands/create/',
                {'title': 'No', 'machine': self.foreign_machine.pk},
            ),
        ):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(
                    self.api.post(endpoint, payload, format='json').status_code, 404
                )
        self.foreign.refresh_from_db()
        self.assertEqual(self.foreign.title, 'Foreign work')
        self.assertEqual(self.foreign.lifecycle_version, 1)

    def test_archiving_parts_and_dependency_deletion_cannot_bypass_scope(self):
        """Object lookup gates non-command writes and both dependency endpoints."""
        dependency = WorkOrderDependency.objects.create(
            predecessor=self.foreign, successor=self.own
        )
        self.assertEqual(self.api.delete(self.resource(self.foreign)).status_code, 404)
        self.assertEqual(
            self.api.delete(
                f'/api/kanban/cards/{self.foreign.primary_card.pk}/'
            ).status_code,
            404,
        )
        self.assertEqual(
            self.api.post(self.resource(self.foreign, 'restore/')).status_code, 404
        )
        self.assertEqual(
            self.api.post(self.resource(self.foreign, 'allocate-parts/')).status_code,
            404,
        )
        self.assertEqual(
            self.api.delete(
                self.resource(self.foreign, f'parts/{self.foreign_part.pk}/')
            ).status_code,
            404,
        )
        self.assertEqual(
            self.api.delete(f'/api/kanban/dependencies/{dependency.pk}/').status_code,
            404,
        )
        self.assertTrue(WorkOrderDependency.objects.filter(pk=dependency.pk).exists())

    def test_planning_update_cannot_move_owned_work_to_a_foreign_machine(self):
        """A valid parent ID does not authorize the proposed destination scope."""
        response = self.api.post(
            self.resource(self.own, 'commands/update/'),
            {
                'fields': {'machine_id': self.foreign_machine.pk},
                'expected_version': 1,
                'idempotency_key': 'scope-reassignment',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 404)
        self.own.refresh_from_db()
        self.assertEqual(self.own.machine_id, self.machine.pk)

    def test_attachment_metadata_and_mutations_authorize_the_exact_owner(self):
        """A model role does not authorize every work-order or machine file."""
        allowed_ids = set()
        denied = []
        for model_type, owner, accessible in (
            ('workorder', self.own, True),
            ('workorder', self.foreign, False),
            ('workorder', self.orphan, True),
            ('assetmachine', self.machine, True),
            ('assetmachine', self.foreign_machine, False),
        ):
            attachment = Attachment.objects.create(
                model_type=model_type,
                model_id=owner.pk,
                link='https://example.invalid/synthetic-evidence',
            )
            if accessible:
                allowed_ids.add(attachment.pk)
                self.assertEqual(
                    self.api.get(f'/api/attachment/{attachment.pk}/').status_code, 200
                )
            else:
                denied.append(attachment)
        response = self.api.get('/api/attachment/')
        self.assertEqual(response.status_code, 200)
        items = (
            response.data['results']
            if isinstance(response.data, dict)
            else response.data
        )
        self.assertEqual({item['pk'] for item in items}, allowed_ids)
        for attachment in denied:
            endpoint = f'/api/attachment/{attachment.pk}/'
            self.assertEqual(self.api.get(endpoint).status_code, 404)
            self.assertEqual(
                self.api.patch(endpoint, {'comment': 'No'}, format='json').status_code,
                404,
            )
            self.assertEqual(self.api.delete(endpoint).status_code, 404)
            self.assertTrue(Attachment.objects.filter(pk=attachment.pk).exists())

    def test_attachment_creation_and_reassignment_cannot_change_scope(self):
        """Validate the proposed generic owner before saving any attachment."""
        denied = self.api.post(
            '/api/attachment/',
            {
                'model_type': 'workorder',
                'model_id': self.foreign.pk,
                'link': 'https://example.invalid/denied',
            },
            format='json',
        )
        self.assertEqual(denied.status_code, 404, denied.data)
        allowed = self.api.post(
            '/api/attachment/',
            {
                'model_type': 'workorder',
                'model_id': self.own.pk,
                'link': 'https://example.invalid/allowed',
            },
            format='json',
        )
        self.assertEqual(allowed.status_code, 201, allowed.data)
        moved = self.api.patch(
            f'/api/attachment/{allowed.data["pk"]}/',
            {
                'model_type': 'assetmachine',
                'model_id': self.foreign_machine.pk,
                'expected_revision': allowed.data['expected_revision'],
            },
            format='json',
        )
        self.assertEqual(moved.status_code, 404)
        stored = Attachment.objects.get(pk=allowed.data['pk'])
        self.assertEqual(
            (stored.model_type, stored.model_id), ('workorder', self.own.pk)
        )

    @override_settings(AIMMS_SINGLE_SITE_CLIENT_CODE='o24-step-no-fallback')
    def test_step_attachment_reads_follow_shared_work_without_any_client_grant(self):
        """Native step metadata follows Work view and exact Work machine scope."""
        from tasks.scope import ScopeError, scope_for_actor

        self.customer_owned.machine = self.foreign_machine
        self.customer_owned.save(update_fields=['machine'])
        self.grant.delete()
        with self.assertRaises(ScopeError):
            scope_for_actor(self.actor)
        self.rules.can_add = self.rules.can_change = self.rules.can_delete = False
        self.rules.save()
        allowed = set()
        for work in (self.own, self.foreign, self.customer_owned, self.orphan):
            step = self.step(work)
            attachment = Attachment.objects.create(
                model_type='workorderstepexecution',
                model_id=step.pk,
                link='https://example.invalid/o24-step-evidence',
            )
            readable = work in (self.customer_owned, self.orphan)
            self.assertEqual(
                self.api.get(f'/api/attachment/{attachment.pk}/').status_code,
                200 if readable else 404,
            )
            if readable:
                allowed.add(attachment.pk)
        response = self.api.get('/api/attachment/')
        self.assertEqual(response.status_code, 200)
        rows = (
            response.data.get('results', [])
            if isinstance(response.data, dict)
            else response.data
        )
        self.assertEqual({row['pk'] for row in rows}, allowed)

        self.rules.can_view = False
        self.rules.save()
        for pk in allowed:
            self.assertEqual(self.api.get(f'/api/attachment/{pk}/').status_code, 404)
        response = self.api.get('/api/attachment/')
        self.assertEqual(response.status_code, 200)
        rows = (
            response.data.get('results', [])
            if isinstance(response.data, dict)
            else response.data
        )
        self.assertEqual(rows, [])

    @override_settings(AIMMS_SINGLE_SITE_CLIENT_CODE='o24-step-no-fallback')
    def test_step_attachment_actions_use_work_roles_and_reauthorize_proposed_owner(
        self,
    ):
        """Ignored child-model permissions never substitute for Work action roles."""
        from django.contrib.auth.models import Permission

        self.grant.delete()
        shared_step = self.step(self.customer_owned)
        orphan_step = self.step(self.orphan)
        foreign_step = self.step(self.foreign)
        attachment = Attachment.objects.create(
            model_type='workorderstepexecution',
            model_id=shared_step.pk,
            link='https://example.invalid/o24-step-actions',
        )
        url = f'/api/attachment/{attachment.pk}/'
        payload = {
            'model_type': 'workorderstepexecution',
            'model_id': shared_step.pk,
            'link': 'https://example.invalid/o24-new-step',
        }
        self.rules.can_add = self.rules.can_change = self.rules.can_delete = False
        self.rules.save()
        # Even explicitly assigned raw child-model permissions are not Work authority.
        self.actor.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label='tasks',
                codename__in=(
                    'change_workorderstepexecution',
                    'delete_workorderstepexecution',
                ),
            )
        )
        self.assertEqual(self.api.get(url).status_code, 200)
        self.assertEqual(
            self.api.patch(url, {'comment': 'Denied'}, format='json').status_code, 403
        )
        self.assertEqual(self.api.delete(url).status_code, 403)
        self.assertEqual(
            self.api.post('/api/attachment/', payload, format='json').status_code, 403
        )
        self.assertEqual(
            self.api.delete(
                '/api/attachment/', {'items': [attachment.pk]}, format='json'
            ).status_code,
            403,
        )
        self.assertTrue(Attachment.objects.filter(pk=attachment.pk).exists())

        self.rules.can_change = self.rules.can_delete = True
        self.rules.save()
        self.assertEqual(
            self.api.patch(url, {'comment': 'Allowed'}, format='json').status_code, 200
        )
        created = self.api.post('/api/attachment/', payload, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        for owner_id in (foreign_step.pk, 99999999):
            refused = self.api.patch(url, {'model_id': owner_id}, format='json')
            self.assertEqual(refused.status_code, 404, refused.data)
        attachment.refresh_from_db()
        self.assertEqual(attachment.model_id, shared_step.pk)
        self.assertEqual(
            self.api.patch(
                url, {'model_id': orphan_step.pk}, format='json'
            ).status_code,
            200,
        )
        self.assertEqual(self.api.delete(url).status_code, 204)
        self.assertEqual(
            self.api.delete(
                '/api/attachment/', {'items': [created.data['pk']]}, format='json'
            ).status_code,
            200,
        )
        self.assertFalse(Attachment.objects.filter(pk=created.data['pk']).exists())

    def test_packet_collections_follow_work_scope_before_machine_scope(self):
        """Packets require their own machine scope, including shared customer Work."""
        own = RepairPacket.objects.create(work_order=self.own, machine=self.machine)
        foreign = RepairPacket.objects.create(
            work_order=self.foreign, machine=self.foreign_machine
        )
        customer = RepairPacket.objects.create(
            work_order=self.customer_owned, machine=self.machine
        )
        standalone = RepairPacket.objects.create(machine=self.machine)
        unscoped = RepairPacket.objects.create()
        response = self.api.get('/api/repair/packets/')
        self.assertEqual(response.status_code, 200)
        items = (
            response.data['results']
            if isinstance(response.data, dict)
            else response.data
        )
        self.assertEqual(
            {item['pk'] for item in items}, {own.pk, customer.pk, standalone.pk}
        )
        for packet in (foreign, unscoped):
            self.assertEqual(
                self.api.get(f'/api/repair/packets/{packet.pk}/').status_code, 404
            )
        self.grant.delete()
        self.assertEqual(
            self.api.get(f'/api/repair/packets/{own.pk}/').status_code, 404
        )

    def test_packet_leaf_reads_actions_and_gate_paths_check_the_same_parent(self):
        """Nested read/write handlers cannot resolve a foreign packet or gate."""
        packet = RepairPacket.objects.create(
            work_order=self.foreign, machine=self.foreign_machine
        )
        gate = RepairPacketGate.objects.create(
            packet=packet, name='Synthetic gate', gate_type='loto'
        )
        for suffix in (
            'generation-status/',
            'gates/',
            'findings/',
            'approved-scope/',
            'start/',
        ):
            self.assertEqual(
                self.api.get(f'/api/repair/packets/{packet.pk}/{suffix}').status_code,
                404,
                suffix,
            )
        for suffix in (
            'generate/',
            'resolve-gates/',
            'verify-diagnosis/',
            'advance/',
            'close/',
            'findings/',
            'approved-scope/',
            'start/',
            'cancel/',
        ):
            self.assertEqual(
                self.api.post(
                    f'/api/repair/packets/{packet.pk}/{suffix}', {}, format='json'
                ).status_code,
                404,
                suffix,
            )
        for action in ('confirm', 'verify', 'waive', 'proofs', 'lockout'):
            self.assertEqual(
                self.api.post(
                    f'/api/repair/packets/{packet.pk}/gates/{gate.pk}/{action}/',
                    {},
                    format='json',
                ).status_code,
                404,
                action,
            )
        self.assertEqual(
            self.api.delete(f'/api/repair/packets/{packet.pk}/').status_code, 404
        )

    def test_packet_reassignment_and_creation_require_the_proposed_owner(self):
        """Direct packet writes cannot introduce a foreign work or asset link."""
        packet = RepairPacket.objects.create(machine=self.machine)
        moved = self.api.patch(
            f'/api/repair/packets/{packet.pk}/',
            {'machine': self.foreign_machine.pk},
            format='json',
        )
        self.assertEqual(moved.status_code, 404)
        created = self.api.post(
            '/api/repair/packets/',
            {'machine': self.foreign_machine.pk, 'fault_summary': 'Denied'},
            format='json',
        )
        self.assertEqual(created.status_code, 404, created.data)

    def test_machine_repair_chooser_only_returns_accessible_work(self):
        """An authorized asset includes customer Work; foreign assets stay refused."""
        owned = RepairPacket.objects.create(work_order=self.own, machine=self.machine)
        customer = RepairPacket.objects.create(
            work_order=self.customer_owned, machine=self.machine
        )
        response = self.api.get(f'/api/repair/machines/{self.machine.pk}/open-repairs/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {item['packet_id'] for item in response.data['results']},
            {owned.pk, customer.pk},
        )
        self.assertEqual(
            self.api.get(
                f'/api/repair/machines/{self.foreign_machine.pk}/open-repairs/'
            ).status_code,
            404,
        )

    def test_viewer_cannot_mutate_an_accessible_packet_or_gate(self):
        """An object grant never substitutes for the native action role."""
        self.rules.can_add = False
        self.rules.can_change = False
        self.rules.can_delete = False
        self.rules.save()
        packet = RepairPacket.objects.create(work_order=self.own, machine=self.machine)
        gate = RepairPacketGate.objects.create(packet=packet, name='Read-only gate')
        self.assertEqual(
            self.api.get(f'/api/repair/packets/{packet.pk}/').status_code, 200
        )
        for suffix in (
            'generate/',
            'resolve-gates/',
            'verify-diagnosis/',
            'advance/',
            'close/',
            'findings/',
            'approved-scope/',
            'start/',
            'cancel/',
        ):
            self.assertEqual(
                self.api.post(
                    f'/api/repair/packets/{packet.pk}/{suffix}', {}, format='json'
                ).status_code,
                403,
                suffix,
            )
        for action in ('confirm', 'verify', 'waive', 'proofs', 'lockout'):
            self.assertEqual(
                self.api.post(
                    f'/api/repair/packets/{packet.pk}/gates/{gate.pk}/{action}/',
                    {},
                    format='json',
                ).status_code,
                403,
                action,
            )
        self.assertEqual(
            self.api.patch(
                f'/api/repair/packets/{packet.pk}/', {}, format='json'
            ).status_code,
            403,
        )
        self.assertEqual(
            self.api.delete(f'/api/repair/packets/{packet.pk}/').status_code, 403
        )
        self.assertEqual(
            self.api.post(
                '/api/repair/packets/', {'machine': self.machine.pk}, format='json'
            ).status_code,
            403,
        )
        self.assertEqual(packet.events.count(), 0)

    def test_work_package_creation_denies_foreign_or_missing_assets(self):
        """Denied assets remain indistinguishable and creation has no side effect."""
        before = WorkOrder.objects.count()
        for machine_id in (self.foreign_machine.pk, 987654):
            response = self.api.post(
                '/api/maintenance/work-packages/create/',
                {'machine_id': machine_id, 'title': 'Denied creation'},
                format='json',
            )
            self.assertEqual(response.status_code, 404, response.data)
        self.assertEqual(WorkOrder.objects.count(), before)

    def test_work_package_duplicate_control_does_not_reveal_hidden_links(self):
        """An override cannot reveal or bypass repair work outside the boundary."""
        RepairPacket.objects.create(
            work_order=self.foreign, machine=self.machine
        )
        before = WorkOrder.objects.count()
        for override in (False, True):
            response = self.api.post(
                '/api/maintenance/work-packages/create/',
                {
                    'machine_id': self.machine.pk,
                    'title': 'Duplicate probe',
                    'duplicate_override': override,
                },
                format='json',
            )
            self.assertEqual(response.status_code, 404, response.data)
            self.assertNotIn('duplicates', response.data)
        self.assertEqual(WorkOrder.objects.count(), before)

    def test_work_package_replay_checks_the_current_owner_before_service(self):
        """A previously issued key cannot modify work after its scope changes."""
        WorkOrderCommand.objects.create(
            work_order=self.foreign,
            command='create',
            idempotency_key='synthetic-hidden-key',
            correlation_id=uuid.uuid4(),
            request_hash='synthetic-only',
            status='succeeded',
        )
        response = self.api.post(
            '/api/maintenance/work-packages/create/',
            {
                'machine_id': self.machine.pk,
                'title': 'Denied replay',
                'idempotency_key': 'synthetic-hidden-key',
                'planning': {'estimated_minutes': 120},
            },
            format='json',
        )
        self.assertEqual(response.status_code, 404, response.data)
        self.customer_owned.refresh_from_db()
        self.assertIsNone(self.customer_owned.estimated_minutes)
