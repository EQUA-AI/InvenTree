"""Transactional native placement capture with independent source readback."""

from integration_state.tests.assertions import assert_literal_bool

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings

from assets.locations import save_location, transfer_machines
from assets.models import AssetMachine, Client, ClientScopeGrant
from integration_state.models import SourceNamespace, SourceState


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class PlacementCaptureTests(TestCase):
    """A native committed write, not a callback assertion, owns the expected state."""

    def setUp(self):
        """Create a real native machine before enabling source capture."""
        self.owner = Client.objects.create(
            name='Synthetic capture owner', code='s5-cap'
        )
        self.actor = get_user_model().objects.create_superuser(
            username='s5-capture-operator', password=None
        )
        ClientScopeGrant.objects.create(user=self.actor, client=self.owner)
        self.destination = save_location(
            self.actor,
            {
                'client': self.owner.pk,
                'name': 'Synthetic destination',
                'code': 'synthetic-destination',
                'timezone': 'UTC',
                'reason': 'Synthetic test setup',
            },
        )
        self.machine = AssetMachine.objects.create(
            name='Synthetic moved machine', client=self.owner
        )
        self.namespace = SourceNamespace.objects.create(
            environment='synthetic-s5', active=True
        )

    def test_native_transfer_commits_current_marker_and_change(self):
        """Actual source, native receipt and durable capture agree after transfer."""
        from uuid import uuid4

        result = transfer_machines(
            self.actor,
            {
                'machines': [
                    {'machine_id': self.machine.pk, 'expected_placement_version': 0}
                ],
                'destination_location_id': self.destination.pk,
                'reason': 'Synthetic native move',
                'idempotency_key': uuid4(),
            },
        )
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.placement_version, 1)
        self.assertEqual(self.machine.physical_location_id, self.destination.pk)
        self.assertFalse(result['replayed'])
        self.assertEqual(SourceState.objects.count(), 1)
        state = SourceState.objects.get()
        self.assertEqual(state.source_id, self.machine.pk)
        self.assertEqual(state.client_id, self.owner.pk)
        self.assertEqual(state.location_id, self.destination.pk)
        self.assertEqual(state.native_version, 1)
        self.assertEqual(state.revision, 1)
        self.assertEqual(state.epoch, self.namespace.epoch)
        self.assertFalse(state.deleted)

        from integration_state.models import SourceChange

        change = SourceChange.objects.get()
        self.assertEqual(change.revision, state.revision)
        self.assertEqual(change.operation, 'changed')
        self.assertEqual(
            change.previous,
            {'client_id': self.owner.pk, 'location_id': None, 'native_version': 0},
        )
        self.assertEqual(
            change.current,
            {
                'client_id': self.owner.pk,
                'location_id': self.destination.pk,
                'native_version': 1,
            },
        )

    def test_creation_is_captured_without_a_transfer_or_actor_enrollment(self):
        """A new source record cannot disappear from the enrolled population."""
        machine = AssetMachine.objects.create(
            name='Synthetic new source', client=self.owner
        )
        self.assertEqual(SourceState.objects.count(), 1)
        state = SourceState.objects.get()
        self.assertEqual(state.source_id, machine.pk)
        self.assertEqual(state.native_version, 0)
        self.assertIsNone(state.location_id)

        from integration_state.models import SourceChange

        change = SourceChange.objects.get()
        self.assertEqual(change.operation, 'created')
        self.assertIsNone(change.previous)
        self.assertEqual(change.current['client_id'], self.owner.pk)

    def test_actual_source_deletion_is_retained_without_protecting_native_row(self):
        """Removal is explicit evidence, not inference from an incomplete scan."""
        machine_id = self.machine.pk
        self.machine.delete()
        self.assertFalse(AssetMachine.objects.filter(pk=machine_id).exists())
        self.assertEqual(SourceState.objects.count(), 1)
        state = SourceState.objects.get()
        self.assertEqual(state.source_id, machine_id)
        self.assertTrue(state.deleted)

        from integration_state.models import SourceChange

        change = SourceChange.objects.get()
        self.assertEqual(change.operation, 'deleted')
        self.assertEqual(change.previous['client_id'], self.owner.pk)
        self.assertIsNone(change.current)

    def test_rollback_removes_source_receipt_history_marker_and_event(self):
        """A rolled-back native transfer has no integration success residue."""
        from uuid import uuid4

        from assets.models import MachineLocationTransfer, MachinePlacementHistory
        from integration_state.models import SourceChange

        with self.assertRaisesRegex(RuntimeError, 'synthetic rollback'):
            with transaction.atomic():
                transfer_machines(
                    self.actor,
                    {
                        'machines': [
                            {
                                'machine_id': self.machine.pk,
                                'expected_placement_version': 0,
                            }
                        ],
                        'destination_location_id': self.destination.pk,
                        'reason': 'Synthetic rolled-back move',
                        'idempotency_key': uuid4(),
                    },
                )
                self.assertTrue(SourceState.objects.exists())
                raise RuntimeError('synthetic rollback')
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.placement_version, 0)
        self.assertIsNone(self.machine.physical_location_id)
        self.assertFalse(SourceState.objects.exists())
        self.assertFalse(SourceChange.objects.exists())
        self.assertFalse(MachineLocationTransfer.objects.exists())
        self.assertFalse(MachinePlacementHistory.objects.exists())

    def test_local_event_constraint_failure_cannot_commit_native_transfer(self):
        """A real trigger INSERT failure rolls back the whole native command."""
        from uuid import uuid4

        from assets.models import MachineLocationTransfer, MachinePlacementHistory
        from integration_state.models import SourceChange

        SourceChange.objects.create(
            namespace=self.namespace,
            epoch=self.namespace.epoch,
            entity_kind='machine',
            aspect='placement',
            source_id=self.machine.pk,
            revision=1,
            operation='baseline',
            previous=None,
            current=None,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            transfer_machines(
                self.actor,
                {
                    'machines': [
                        {'machine_id': self.machine.pk, 'expected_placement_version': 0}
                    ],
                    'destination_location_id': self.destination.pk,
                    'reason': 'Synthetic event-constraint fault',
                    'idempotency_key': uuid4(),
                },
            )
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.placement_version, 0)
        self.assertIsNone(self.machine.physical_location_id)
        self.assertFalse(SourceState.objects.exists())
        self.assertEqual(SourceChange.objects.count(), 1)
        self.assertFalse(MachineLocationTransfer.objects.exists())
        self.assertFalse(MachinePlacementHistory.objects.exists())

    def test_bulk_placement_aba_advances_fence_without_native_version(self):
        """Native-counter and timestamp-only detectors miss these actual writes."""
        from integration_state.models import SourceChange

        AssetMachine.objects.filter(pk=self.machine.pk).update(
            physical_location_id=self.destination.pk
        )
        first = SourceState.objects.get()
        AssetMachine.objects.filter(pk=self.machine.pk).update(
            physical_location_id=None
        )
        second = SourceState.objects.get()
        self.assertEqual((first.revision, second.revision), (1, 2))
        self.assertEqual((first.native_version, second.native_version), (0, 0))
        self.assertIsNone(second.location_id)
        self.assertEqual(SourceChange.objects.count(), 2)
        AssetMachine.objects.filter(pk=self.machine.pk).update(name='Synthetic renamed')
        self.assertEqual(SourceState.objects.get().revision, 2)
        self.assertEqual(SourceChange.objects.count(), 2)

    def test_replayed_old_receipt_cannot_regress_newer_captured_placement(self):
        """Client locking side effects are not new placement events."""
        from uuid import uuid4

        from assets.models import MachineLocationTransfer, MachinePlacementHistory
        from integration_state.models import SourceChange

        values = {
            'machines': [
                {'machine_id': self.machine.pk, 'expected_placement_version': 0}
            ],
            'destination_location_id': self.destination.pk,
            'reason': 'Synthetic initial move',
            'idempotency_key': uuid4(),
        }
        first = transfer_machines(self.actor, values)
        replay = transfer_machines(self.actor, values)
        self.assertEqual(replay['transfer_id'], first['transfer_id'])
        self.assertTrue(replay['replayed'])
        self.assertEqual(SourceState.objects.get().revision, 1)
        transfer_machines(
            self.actor,
            {
                'machines': [
                    {'machine_id': self.machine.pk, 'expected_placement_version': 1}
                ],
                'destination_location_id': None,
                'reason': 'Synthetic later move',
                'idempotency_key': uuid4(),
            },
        )
        replay = transfer_machines(self.actor, values)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['machines'][0]['placement_version'], 1)
        state = SourceState.objects.get()
        self.assertEqual((state.revision, state.native_version), (2, 2))
        self.assertIsNone(state.location_id)
        self.assertEqual(SourceChange.objects.count(), 2)
        self.assertEqual(MachineLocationTransfer.objects.count(), 2)
        self.assertEqual(MachinePlacementHistory.objects.count(), 2)


class CaptureInstallationTests(TestCase):
    """Unsupported source schemas must not attach capture to another foundation."""

    def test_capture_catalogue_is_verified_and_missing_trigger_is_unqualified(self):
        """A source population cannot be enrolled with a missing capture event."""
        from integration_state.capture import (
            UPDATE_TRIGGER,
            capture_is_installed,
            capture_statements,
        )

        assert_literal_bool(self, capture_is_installed(connection), True)
        update = next(
            sql
            for sql in capture_statements(connection.vendor)
            if sql.split()[:3] == ['CREATE', 'TRIGGER', UPDATE_TRIGGER]
        )
        suffix = (
            ' ON public.assets_assetmachine'
            if connection.vendor == 'postgresql'
            else ''
        )
        with connection.cursor() as cursor:
            cursor.execute(f'DROP TRIGGER {UPDATE_TRIGGER}{suffix}')
        try:
            assert_literal_bool(self, capture_is_installed(connection), False)
        finally:
            with connection.cursor() as cursor:
                cursor.execute(update)
        assert_literal_bool(self, capture_is_installed(connection), True)

    def test_same_named_noop_capture_is_not_verified(self):
        """Names alone cannot establish transactional writer coverage."""
        from integration_state.capture import (
            CAPTURE_FUNCTION,
            UPDATE_TRIGGER,
            capture_is_installed,
        )

        assert_literal_bool(self, capture_is_installed(connection), True)
        with connection.cursor() as cursor:
            if connection.vendor == 'sqlite':
                cursor.execute(f'DROP TRIGGER {UPDATE_TRIGGER}')
                cursor.execute(
                    f'CREATE TRIGGER {UPDATE_TRIGGER} AFTER UPDATE '
                    'ON assets_assetmachine BEGIN SELECT 1; END'
                )
            else:
                cursor.execute(
                    f'CREATE OR REPLACE FUNCTION public.{CAPTURE_FUNCTION}() '
                    'RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER '
                    'SET search_path = pg_catalog, public '
                    'AS $$ BEGIN RETURN NULL; END; $$'
                )
        assert_literal_bool(self, capture_is_installed(connection), False)

    def test_disabled_postgres_capture_is_unqualified_until_explicitly_restored(self):
        """Exercise the already-implemented enabled-state check on the real vendor."""
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL trigger enablement has no SQLite equivalent')
        from integration_state.capture import UPDATE_TRIGGER, capture_is_installed

        assert_literal_bool(self, capture_is_installed(connection), True)
        with connection.cursor() as cursor:
            cursor.execute(
                'ALTER TABLE public.assets_assetmachine '
                f'DISABLE TRIGGER {UPDATE_TRIGGER}'
            )
        assert_literal_bool(self, capture_is_installed(connection), False)
        with connection.cursor() as cursor:
            cursor.execute(
                'ALTER TABLE public.assets_assetmachine '
                f'ENABLE TRIGGER {UPDATE_TRIGGER}'
            )
        assert_literal_bool(self, capture_is_installed(connection), True)

    def test_postgres_nonpublic_schema_is_refused_before_any_installation(self):
        """Public-qualified capture cannot silently enroll a different search path."""
        if connection.vendor != 'postgresql':
            self.skipTest('The qualified PostgreSQL schema guard is vendor-specific')
        from integration_state.capture import install_capture, uninstall_capture

        uninstall_capture(connection)
        with self.assertRaisesRegex(ValueError, 'schema'), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET LOCAL search_path = pg_catalog')
            install_capture(connection)

    def test_postgres_nonpublic_schema_is_refused_before_capture_removal(self):
        """The reverse operation has the same explicit source-schema boundary."""
        if connection.vendor != 'postgresql':
            self.skipTest('The qualified PostgreSQL schema guard is vendor-specific')
        from integration_state.capture import uninstall_capture

        with self.assertRaisesRegex(ValueError, 'schema'), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET LOCAL search_path = pg_catalog')
            uninstall_capture(connection)
