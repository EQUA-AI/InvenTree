"""Contract tests for physical hierarchy, scope and effective placement."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.db import close_old_connections
from django.test import (
    TestCase,
    TransactionTestCase,
    override_settings,
    skipUnlessDBFeature,
)

from rest_framework.exceptions import APIException
from rest_framework.test import APIClient
from tasks.models import WorkOrder, WorkOrderLifecycle

from assets.locations import save_location, transfer_machines
from assets.models import (
    AssetLocation,
    AssetMachine,
    Client,
    ClientScopeGrant,
    LocationParentHistory,
    MachineLocationTransfer,
    MachinePlacementHistory,
)
from users.models import RuleSet

BASE = '/api/assets/locations/'


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class PhysicalLocationTests(TestCase):
    """Exercise real API permissions, full-population counts and atomic writes."""

    def setUp(self):
        """Grant an operator one of two clients."""
        ContentType.objects.clear_cache()
        self.owner = Client.objects.create(name='Location owner', code='loc-owner')
        self.foreign = Client.objects.create(name='Other owner', code='loc-other')
        self.user = get_user_model().objects.create_superuser(
            username='location-operator', password=None
        )
        ClientScopeGrant.objects.create(user=self.user, client=self.owner)
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.site = self.node('Site')
        self.room = self.node('Room', parent=self.site.pk, timezone='')
        self.machine = AssetMachine.objects.create(
            name='Location test machine', client=self.owner, location='Old paper label'
        )

    def node(self, name, **extra):
        """Create through the same structural command used by the API."""
        return save_location(
            self.user,
            {
                'client': self.owner.pk,
                'name': name,
                'code': name.lower().replace(' ', '-'),
                'timezone': 'UTC',
                'reason': 'Test setup',
                **extra,
            },
        )

    def move(self, destination, **extra):
        """Build a valid transfer from the current persisted machine version."""
        self.machine.refresh_from_db()
        return {
            'machines': [
                {
                    'machine_id': self.machine.pk,
                    'expected_placement_version': self.machine.placement_version,
                }
            ],
            'destination_location_id': destination,
            'reason': 'Planned move',
            'idempotency_key': str(uuid.uuid4()),
            **extra,
        }

    def test_create_any_level_and_inherit_timezone(self):
        """A nested room keeps an authorized full path and inherited timezone."""
        response = self.api.get(f'{BASE}{self.room.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['effective_timezone'], 'UTC')
        self.assertEqual(
            [p['pk'] for p in response.data['path']], [self.site.pk, self.room.pk]
        )
        self.assertEqual(
            LocationParentHistory.objects.filter(location=self.room).count(), 1
        )

    def test_roots_require_valid_timezone(self):
        """A root cannot enable misleading civil-date reporting."""
        for zone in ('', 'Not/A_Zone'):
            response = self.api.post(
                BASE,
                {
                    'client': self.owner.pk,
                    'name': 'New',
                    'code': 'new',
                    'timezone': zone,
                    'reason': 'New site',
                },
                format='json',
            )
            self.assertEqual(response.status_code, 400)

    def test_sibling_names_normalized_and_roots_unique(self):
        """Case and whitespace do not make a second identical root."""
        response = self.api.post(
            BASE,
            {
                'client': self.owner.pk,
                'name': ' SITE ',
                'code': 'different',
                'timezone': 'UTC',
                'reason': 'Duplicate',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.node('Room', code='second-room', parent=None)

    def test_descendant_cycle_rejected(self):
        """Reparenting under a descendant is rejected and history unchanged."""
        response = self.api.patch(
            f'{BASE}{self.site.pk}/',
            {'parent': self.room.pk, 'expected_version': 1, 'reason': 'Move'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.site.refresh_from_db()
        self.assertIsNone(self.site.parent_id)
        self.assertEqual(self.site.version, 1)

    def test_version_conflict_and_metadata_history(self):
        """Only the first editor's update commits."""
        payload = {'name': 'Renamed site', 'expected_version': 1, 'reason': 'Rename'}
        self.assertEqual(
            self.api.patch(
                f'{BASE}{self.site.pk}/', payload, format='json'
            ).status_code,
            200,
        )
        self.assertEqual(
            self.api.patch(
                f'{BASE}{self.site.pk}/', payload, format='json'
            ).status_code,
            409,
        )
        history = list(
            LocationParentHistory.objects.filter(location=self.site).order_by(
                'valid_from'
            )
        )
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].valid_to, history[1].valid_from)
        self.assertEqual(history[0].snapshot['name'], 'Site')

    def test_reparent_preserves_event_time_ancestry(self):
        """New parent membership starts at the command's effective time."""
        other = self.node('Other')
        response = self.api.patch(
            f'{BASE}{self.room.pk}/',
            {'parent': other.pk, 'expected_version': 1, 'reason': 'Reorganize'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        rows = list(self.room.parent_history.order_by('valid_from'))
        self.assertEqual([r.parent_id for r in rows], [self.site.pk, other.pk])
        self.assertEqual(rows[0].valid_to, rows[1].valid_from)

    def test_archive_blocks_active_children_and_machines(self):
        """Retirement cannot strand active descendants or assigned machines."""
        self.assertEqual(
            self.api.patch(
                f'{BASE}{self.site.pk}/',
                {'archived': True, 'expected_version': 1, 'reason': 'Retire'},
                format='json',
            ).status_code,
            400,
        )
        self.api.post(f'{BASE}transfers/', self.move(self.room.pk), format='json')
        self.assertEqual(
            self.api.patch(
                f'{BASE}{self.room.pk}/',
                {'archived': True, 'expected_version': 1, 'reason': 'Retire'},
                format='json',
            ).status_code,
            400,
        )

    def test_archived_target_rejected(self):
        """An archived empty room cannot receive a machine."""
        self.assertEqual(
            self.api.patch(
                f'{BASE}{self.room.pk}/',
                {'archived': True, 'expected_version': 1, 'reason': 'Retire'},
                format='json',
            ).status_code,
            200,
        )
        self.assertEqual(
            self.api.post(
                f'{BASE}transfers/', self.move(self.room.pk), format='json'
            ).status_code,
            400,
        )

    def test_transfer_history_and_legacy_text(self):
        """Transfers preserve old text and contiguous effective history."""
        self.assertEqual(
            self.api.post(
                f'{BASE}transfers/', self.move(self.site.pk), format='json'
            ).status_code,
            200,
        )
        self.assertEqual(
            self.api.post(
                f'{BASE}transfers/', self.move(self.room.pk), format='json'
            ).status_code,
            200,
        )
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.location, 'Old paper label')
        self.assertEqual(self.machine.physical_location_id, self.room.pk)
        rows = list(self.machine.placement_history.order_by('valid_from'))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].valid_to, rows[1].valid_from)
        self.assertIsNone(rows[1].valid_to)

    def test_idempotent_replay_and_conflicting_payload(self):
        """Retry is safe, while reusing a key for another request is rejected."""
        payload = self.move(self.site.pk)
        first = self.api.post(f'{BASE}transfers/', payload, format='json')
        second = self.api.post(f'{BASE}transfers/', payload, format='json')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.data['replayed'])
        self.assertEqual(first.data['transfer_id'], second.data['transfer_id'])
        self.assertEqual(MachinePlacementHistory.objects.count(), 1)
        payload['destination_location_id'] = self.room.pk
        self.assertEqual(
            self.api.post(f'{BASE}transfers/', payload, format='json').status_code, 409
        )

    def test_stale_batch_rolls_back_every_machine(self):
        """No partial batch succeeds when one selected version is stale."""
        other = AssetMachine.objects.create(name='Second machine', client=self.owner)
        payload = self.move(self.site.pk)
        payload['machines'].append({
            'machine_id': other.pk,
            'expected_placement_version': 7,
        })
        self.assertEqual(
            self.api.post(f'{BASE}transfers/', payload, format='json').status_code, 409
        )
        self.assertEqual(MachineLocationTransfer.objects.count(), 0)
        self.machine.refresh_from_db()
        self.assertIsNone(self.machine.physical_location_id)

    def test_cross_client_batch_rejected(self):
        """Even a superuser cannot mix ungranted client assets into a move."""
        other = AssetMachine.objects.create(name='Foreign machine', client=self.foreign)
        payload = self.move(self.site.pk)
        payload['machines'].append({
            'machine_id': other.pk,
            'expected_placement_version': 0,
        })
        self.assertEqual(
            self.api.post(f'{BASE}transfers/', payload, format='json').status_code, 400
        )
        self.assertEqual(MachineLocationTransfer.objects.count(), 0)

    def test_cross_client_parent_and_hidden_location(self):
        """Unauthorized locations cannot leak through paths or direct access."""
        foreign = AssetLocation.objects.create(
            client=self.foreign,
            name='Secret',
            code='secret',
            name_key='secret',
            sibling_key='root',
            timezone='UTC',
        )
        self.assertEqual(self.api.get(f'{BASE}{foreign.pk}/').status_code, 404)
        self.assertEqual(
            self.api.patch(
                f'{BASE}{self.room.pk}/',
                {'parent': foreign.pk, 'expected_version': 1, 'reason': 'Move'},
                format='json',
            ).status_code,
            400,
        )
        self.assertNotIn('Secret', str(self.api.get(BASE).data))

    def test_duplicate_machine_and_unsupported_backdating(self):
        """The ordinary endpoint cannot silently accept temporal corrections."""
        payload = self.move(self.site.pk)
        payload['machines'] *= 2
        self.assertEqual(
            self.api.post(f'{BASE}transfers/', payload, format='json').status_code, 400
        )
        payload = self.move(self.site.pk, effective_at='2020-01-01T00:00:00Z')
        self.assertEqual(
            self.api.post(f'{BASE}transfers/', payload, format='json').status_code, 400
        )

    def test_counts_direct_descendants_and_unassigned(self):
        """Counts use the full authorized population, not a table page."""
        other = AssetMachine.objects.create(
            name='Other room machine', client=self.owner
        )
        transfer_machines(self.user, self.move(self.site.pk))
        transfer_machines(
            self.user,
            {
                'machines': [{'machine_id': other.pk, 'expected_placement_version': 0}],
                'destination_location_id': self.room.pk,
                'reason': 'Move',
                'idempotency_key': uuid.uuid4(),
            },
        )
        AssetMachine.objects.create(name='Unassigned machine', client=self.owner)
        AssetMachine.objects.create(name='Hidden machine', client=self.foreign)
        for state in (WorkOrderLifecycle.PLANNED, WorkOrderLifecycle.DRAFT):
            WorkOrder.objects.create(
                title=state,
                machine=self.machine,
                priority='high',
                status='backlog',
                lifecycle_status=state,
            )
        detail = self.api.get(f'{BASE}{self.site.pk}/').data
        self.assertEqual(
            detail['counts'],
            {
                'direct_machines': 1,
                'total_machines': 2,
                'direct_open_work_orders': 1,
                'total_open_work_orders': 1,
            },
        )
        self.assertEqual(
            self.api.get(
                f'{BASE}machines/', {'location': self.site.pk, 'limit': 1}
            ).data['count'],
            2,
        )
        self.assertEqual(
            self.api.get(
                f'{BASE}machines/',
                {'location': self.site.pk, 'include_descendants': 'false'},
            ).data['count'],
            1,
        )
        self.assertEqual(
            self.api.get(f'{BASE}machines/', {'unassigned': 'true'}).data['count'], 1
        )

    def test_unassignment_is_a_real_history_entry(self):
        """Null is an explicit new placement, not a fake physical node."""
        transfer_machines(self.user, self.move(self.site.pk))
        transfer_machines(self.user, self.move(None))
        current = self.machine.placement_history.get(valid_to__isnull=True)
        self.assertIsNone(current.location_id)

    def test_legacy_api_cannot_change_placed_machine_client(self):
        """Legacy writes cannot break the new client/location invariant."""
        transfer_machines(self.user, self.move(self.site.pk))
        response = self.api.patch(
            f'/api/assets/machines/{self.machine.pk}/',
            {'client': self.foreign.pk},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_viewer_can_read_but_cannot_mutate(self):
        """UI-hidden controls are also denied by backend role enforcement."""
        viewer = get_user_model().objects.create_user(username='location-viewer')
        group = Group.objects.create(name='Location viewers')
        RuleSet.objects.update_or_create(
            group=group,
            name='work_order',
            defaults={
                'can_view': True,
                'can_add': False,
                'can_change': False,
                'can_delete': False,
            },
        )
        viewer.groups.add(group)
        ClientScopeGrant.objects.create(user=viewer, client=self.owner)
        self.api.force_authenticate(viewer)
        self.assertEqual(self.api.get(BASE).status_code, 200)
        self.assertEqual(
            self.api.post(
                f'{BASE}transfers/', self.move(self.site.pk), format='json'
            ).status_code,
            403,
        )
        self.assertEqual(
            self.api.patch(
                f'{BASE}{self.site.pk}/',
                {'name': 'Forbidden', 'reason': 'Edit', 'expected_version': 1},
                format='json',
            ).status_code,
            403,
        )

    def test_legacy_edit_keeps_a_concurrently_committed_placement(self):
        """An editor loaded before a move cannot overwrite the new placement."""
        from assets.serializers import AssetMachineSerializer

        serializer = AssetMachineSerializer(
            self.machine, data={'name': 'Renamed'}, partial=True
        )
        self.assertTrue(serializer.is_valid())
        transfer_machines(self.user, self.move(self.room.pk))
        serializer.save()
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.physical_location_id, self.room.pk)
        self.assertEqual(self.machine.placement_version, 1)
        self.assertEqual(self.machine.name, 'Renamed')

    @override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=None)
    def test_unresolved_scope_fails_closed(self):
        """Even an administrative role cannot invent client scope."""
        self.assertEqual(self.api.get(BASE).status_code, 403)

    def test_invalid_filter_not_silently_ignored(self):
        """Invalid scope parameters cannot accidentally become an all view."""
        for query in (
            {'location': 'oops'},
            {'include_descendants': 'yes'},
            {'location': self.site.pk, 'unassigned': 'true'},
        ):
            self.assertEqual(self.api.get(f'{BASE}machines/', query).status_code, 400)


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class ConcurrentLocationTests(TransactionTestCase):
    """Independent DB connections exercise the shared structural write guard."""

    def setUp(self):
        """Create a committed client, two roots and one unassigned machine."""
        ContentType.objects.clear_cache()
        self.owner = Client.objects.create(name='Concurrent owner', code='concurrent')
        self.user = get_user_model().objects.create_superuser(
            username='concurrent-location-operator', password=None
        )
        ClientScopeGrant.objects.create(user=self.user, client=self.owner)
        self.nodes = [
            save_location(
                self.user,
                {
                    'client': self.owner.pk,
                    'name': name,
                    'code': name.lower(),
                    'timezone': 'UTC',
                    'reason': 'Setup',
                },
            )
            for name in ('First', 'Second')
        ]
        self.machine = AssetMachine.objects.create(
            name='Concurrent machine', client=self.owner
        )

    def race(self, operation):
        """Release two workers together, with their own users and connections."""
        barrier = Barrier(2)

        def run(index):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.user.pk)
                barrier.wait(timeout=10)
                try:
                    operation(actor, index)
                    return 'ok'
                except APIException as exc:
                    return exc.status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(run, (0, 1)))

    @skipUnlessDBFeature('has_select_for_update')
    def test_conflicting_reparent_cannot_create_cycle(self):
        """Concurrent A-under-B and B-under-A cannot both commit."""
        result = self.race(
            lambda actor, i: save_location(
                actor,
                {
                    'parent': self.nodes[1 - i].pk,
                    'expected_version': 1,
                    'reason': 'Concurrent edit',
                },
                pk=self.nodes[i].pk,
            )
        )
        self.assertCountEqual(result, ['ok', 400])
        self.assertEqual(LocationParentHistory.objects.count(), 3)

    @skipUnlessDBFeature('has_select_for_update')
    def test_stale_concurrent_transfers_have_one_winner(self):
        """Both callers read version zero; exactly one placement is recorded."""
        result = self.race(
            lambda actor, i: transfer_machines(
                actor,
                {
                    'machines': [
                        {'machine_id': self.machine.pk, 'expected_placement_version': 0}
                    ],
                    'destination_location_id': self.nodes[i].pk,
                    'reason': 'Concurrent move',
                    'idempotency_key': str(uuid.uuid4()),
                },
            )
        )
        self.assertCountEqual(result, ['ok', 409])
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.placement_version, 1)
        self.assertEqual(MachinePlacementHistory.objects.count(), 1)
        self.assertEqual(MachineLocationTransfer.objects.count(), 1)
