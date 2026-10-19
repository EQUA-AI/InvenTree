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
            title='Customer boundary wins', machine=self.machine, customer=customer
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

    def test_collections_include_only_the_canonical_boundary(self):
        """Role access never widens client scope or explicit customer precedence."""
        for endpoint in ('/api/kanban/work-orders/', '/api/tasks/work-orders/'):
            with self.subTest(endpoint=endpoint):
                response = self.api.get(endpoint)
                self.assertEqual(response.status_code, 200)
                items = (
                    response.data['results']
                    if isinstance(response.data, dict)
                    else response.data
                )
                self.assertEqual({item['id'] for item in items}, {self.own.pk})
        cards = self.api.get('/api/kanban/cards/')
        self.assertEqual(cards.status_code, 200)
        self.assertEqual({item['work_order'] for item in cards.data}, {self.own.pk})

    def test_detail_overview_card_and_part_fail_closed(self):
        """Forbidden and unresolved records return scope-safe not-found results."""
        for work_order in (self.foreign, self.customer_owned, self.orphan):
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

    def test_revocation_removes_legacy_access_on_the_next_session_request(self):
        """Revoking a stored grant cannot retain stale legacy list/detail authority."""
        self.assertEqual(self.api.get(self.resource(self.own)).status_code, 200)
        self.grant.delete()
        self.assertEqual(self.api.get('/api/kanban/work-orders/').data, [])
        self.assertEqual(self.api.get('/api/kanban/cards/').data, [])
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
        self.assertEqual({item['id'] for item in window.data['cards']}, {self.own.pk})
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
            ('workorder', self.orphan, False),
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
            {'model_type': 'assetmachine', 'model_id': self.foreign_machine.pk},
            format='json',
        )
        self.assertEqual(moved.status_code, 404)
        stored = Attachment.objects.get(pk=allowed.data['pk'])
        self.assertEqual(
            (stored.model_type, stored.model_id), ('workorder', self.own.pk)
        )

    def test_packet_collections_follow_work_scope_before_machine_scope(self):
        """An owned machine cannot widen the linked work's customer boundary."""
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
        self.assertEqual({item['pk'] for item in items}, {own.pk, standalone.pk})
        for packet in (foreign, customer, unscoped):
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
        """A known asset does not expose customer-owned or foreign packet links."""
        owned = RepairPacket.objects.create(work_order=self.own, machine=self.machine)
        RepairPacket.objects.create(
            work_order=self.customer_owned, machine=self.machine
        )
        response = self.api.get(f'/api/repair/machines/{self.machine.pk}/open-repairs/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item['packet_id'] for item in response.data['results']], [owned.pk]
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
            work_order=self.customer_owned, machine=self.machine
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
            work_order=self.customer_owned,
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
