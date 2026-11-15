"""Retained generic metadata cannot disappear through an additive backout."""

from importlib import import_module
from types import SimpleNamespace
from unittest import skipUnless

from django.apps import apps
from django.conf import settings
from django.db import connection, migrations
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase


class SharedMigrationGuardTests(TestCase):
    """Actual local metadata/storage guard, separate from native executor proof."""

    def test_retained_generic_identity_refuses_reverse_without_removing_state(self):
        """Even private/unqualified source observations have indefinite retention."""
        from integration_state.models import GenericSourceIdentity, SourceNamespace
        from integration_state.shared_contracts import (
            SharedSourceKey,
            SourceObservation,
        )
        from integration_state.shared_events import record_observation
        from integration_state.shared_migration_operations import guard_shared_state

        namespace = SourceNamespace.objects.create(
            environment='synthetic-shared', active=True
        )
        key = SharedSourceKey(
            namespace_id=namespace.pk,
            environment=namespace.environment,
            epoch=namespace.epoch,
            entity_kind='synthetic_document',
            aspect='metadata',
            source_id='private-7',
        )
        record_observation(
            key,
            event_key='retained',
            expected_fence=0,
            observation=SourceObservation(state='available'),
        )
        with self.assertRaises(IrreversibleError):
            guard_shared_state(apps, SimpleNamespace(connection=connection))
        self.assertEqual(GenericSourceIdentity.objects.count(), 1)

    def test_exact_additive_migration_runs_its_reverse_guard_first(self):
        """Schema drop ordering cannot bypass retention or rewrite old nodes."""
        migration = import_module(
            'integration_state.migrations.0003_generic_source_events'
        )
        self.assertEqual(
            migration.Migration.dependencies,
            [('integration_state', '0002_machine_placement_capture')],
        )
        operation = migration.Migration.operations[-1]
        self.assertIsInstance(operation, migrations.RunPython)
        self.assertEqual(operation.reverse_code.__name__, 'guard_shared_state')

    def test_consumer_only_metadata_cannot_be_dropped_by_shared_backout(self):
        """No observed sources is not permission to erase private configuration."""
        from uuid import uuid4

        from integration_state.models import (
            EventConsumer,
            GenericSourceIdentity,
            SourceNamespace,
        )
        from integration_state.shared_delivery import configure_consumer
        from integration_state.shared_migration_operations import guard_shared_state

        namespace = SourceNamespace.objects.create(
            environment='synthetic-config-retention', active=True
        )
        configure_consumer(
            namespace.pk,
            'retained-consumer',
            aspects=(('synthetic_document', 'metadata'),),
            configuration_epoch=uuid4(),
        )
        self.assertFalse(GenericSourceIdentity.objects.exists())
        with self.assertRaises(IrreversibleError):
            guard_shared_state(apps, SimpleNamespace(connection=connection))
        self.assertEqual(EventConsumer.objects.count(), 1)

    def test_scan_only_metadata_cannot_be_dropped_by_shared_backout(self):
        """A retained request and its fixed budgets survive without any events."""
        from integration_state.models import ReconciliationScan, SourceNamespace
        from integration_state.s7_reconciliation import ScanBinding
        from integration_state.shared_migration_operations import guard_shared_state
        from integration_state.shared_reconciliation import begin_scan

        namespace = SourceNamespace.objects.create(
            environment='synthetic-scan-retention', active=True
        )
        binding = ScanBinding(
            environment=namespace.environment,
            epoch=namespace.epoch,
            entity_kind='synthetic_document',
            aspect='metadata',
            actor_key='synthetic-owner',
            client_id=7,
            filter_key='retained-filter',
            generation='fixture-generation',
        )
        begin_scan(
            namespace.pk,
            'retained-scan',
            binding,
            max_pages=2,
            max_records=5,
            authorize=lambda value: True,
        )
        with self.assertRaises(IrreversibleError):
            guard_shared_state(apps, SimpleNamespace(connection=connection))
        self.assertEqual(ReconciliationScan.objects.count(), 1)

    def test_private_intent_only_metadata_cannot_be_dropped_by_shared_backout(self):
        """A private non-actionable request is still indefinitely retained state."""
        from uuid import uuid4

        from assets.integration_contracts import ClientSourceIdentity
        from integration_state.models import PrivateDeletionRequest, SourceNamespace
        from integration_state.s7_contracts import SourceAspectIdentity
        from integration_state.s7_deletion_requests import DeletionIntent
        from integration_state.shared_migration_operations import guard_shared_state
        from integration_state.shared_requests import record_private_intent

        namespace = SourceNamespace.objects.create(
            environment='synthetic-intent-retention', active=True
        )
        source = SourceAspectIdentity(
            environment=namespace.environment,
            epoch=namespace.epoch,
            entity_kind='synthetic_document',
            aspect='metadata',
            source_id='retained-7',
        )
        intent = DeletionIntent(
            request_id=uuid4(),
            request_key='retained-private-request',
            request_revision=1,
            actor_key='synthetic-owner',
            client=ClientSourceIdentity(namespace.environment, 7),
            scope=(source,),
        )
        record_private_intent(namespace.pk, intent, authorize=lambda value: True)
        with self.assertRaises(IrreversibleError):
            guard_shared_state(apps, SimpleNamespace(connection=connection))
        self.assertEqual(PrivateDeletionRequest.objects.count(), 1)


@skipUnless(
    getattr(settings, 'S5_NATIVE_MIGRATIONS', False)
    and connection.vendor == 'postgresql',
    'Requires owned native PostgreSQL migrations',
)
class NativeSharedMigrationTests(TransactionTestCase):
    """Real populated placement0002 → generic0003, not model-sync metadata."""

    def test_actual_private_backout_refuses_retained_request_copy_and_attempt(self):
        """Real0006 reversal refuses retained metadata before any schema/history loss."""
        from uuid import uuid4

        from django.db.migrations.recorder import MigrationRecorder

        from assets.integration_contracts import ClientSourceIdentity
        from integration_state import shared_requests as durable
        from integration_state.models import PrivateCopyAttempt, SourceNamespace
        from integration_state.s7_contracts import SourceAspectIdentity
        from integration_state.s7_deletion_requests import CopyImpact, DeletionIntent

        namespace = SourceNamespace.objects.create(
            environment='synthetic-native-private', active=True
        )
        source = SourceAspectIdentity(
            environment=namespace.environment,
            epoch=namespace.epoch,
            entity_kind='synthetic_document',
            aspect='metadata',
            source_id='retained-private-7',
        )
        intent = DeletionIntent(
            request_id=uuid4(),
            request_key='native-retained-private',
            request_revision=1,
            actor_key='synthetic-owner',
            client=ClientSourceIdentity(namespace.environment, 7),
            scope=(source,),
        )
        request = durable.record_private_intent(
            namespace.pk, intent, authorize=lambda value: True
        )
        impact = CopyImpact(
            copy_key='retained-backup',
            source=source,
            copy_revision=None,
            owners=(intent.client,),
            attribution='source_claimed',
        )
        stored = durable.record_copy_impact(
            request.request_pk,
            impact,
            intent=intent,
            copy_kind='backup',
            authorize=lambda value: True,
        )
        attempt = durable.start_private_copy_attempt(
            stored.impact_pk,
            intent=intent,
            impact=impact,
            attempt=uuid4(),
            max_attempts=2,
            authorize=lambda value: True,
        )
        before = set(MigrationRecorder(connection).applied_migrations())
        with self.assertRaises(IrreversibleError):
            MigrationExecutor(connection).migrate([
                ('integration_state', '0005_reconciliation_progress')
            ])
        self.assertEqual(
            set(MigrationRecorder(connection).applied_migrations()), before
        )
        self.assertTrue(
            PrivateCopyAttempt.objects.filter(pk=attempt.attempt_pk).exists()
        )
        self.assertEqual(
            durable.read_private_copy_progress(
                stored.impact_pk,
                intent=intent,
                impact=impact,
                authorize=lambda value: True,
            ),
            attempt,
        )
        SourceNamespace.objects.update(active=False)

    def test_empty_additive_positions_reverse_and_reapply_without_faking_history(self):
        """Real partial0003-0007 positions preserve native schema and recover forward."""
        from django.db.migrations.recorder import MigrationRecorder

        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes('integration_state')
        self.assertEqual(leaves, [('integration_state', '0007_native_authority')])
        final = leaves[0]
        positions = (
            '0002_machine_placement_capture',
            '0003_generic_source_events',
            '0004_event_delivery',
            '0005_reconciliation_progress',
            '0006_private_request_copy_progress',
            '0007_native_authority',
        )
        try:
            MigrationExecutor(connection).migrate([('integration_state', positions[0])])
            for position in positions[1:]:
                MigrationExecutor(connection).migrate([('integration_state', position)])
                applied = set(MigrationRecorder(connection).applied_migrations())
                expected = {
                    ('integration_state', name)
                    for name in positions
                    if name <= position
                }
                self.assertTrue(expected.issubset(applied))
                self.assertFalse(
                    {
                        ('integration_state', name)
                        for name in positions
                        if name > position
                    }.intersection(applied)
                )
            MigrationExecutor(connection).migrate([
                ('integration_state', '0005_reconciliation_progress')
            ])
            self.assertNotIn(
                'integration_state_privatecopyattempt',
                connection.introspection.table_names(),
            )
            MigrationExecutor(connection).migrate([final])
            self.assertIn(
                'integration_state_privatecopyattempt',
                connection.introspection.table_names(),
            )
        finally:
            MigrationExecutor(connection).migrate([final])

    def test_populated_placement_upgrade_preserves_rows_and_refuses_generic_backout(
        self,
    ):
        """Add generic support without rewriting old native source/history/state."""
        from assets.models import AssetMachine, Client
        from integration_state.models import SourceChange, SourceNamespace, SourceState
        from integration_state.shared_contracts import (
            SharedSourceKey,
            SourceObservation,
        )
        from integration_state.shared_events import record_observation

        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes('integration_state')
        self.assertEqual(len(leaves), 1)
        final = leaves[0]
        executor.migrate([('integration_state', '0002_machine_placement_capture')])
        old_apps = executor.loader.project_state([
            ('integration_state', '0002_machine_placement_capture')
        ]).apps
        old_namespace = old_apps.get_model('integration_state', 'SourceNamespace')
        namespace = old_namespace.objects.create(
            environment='synthetic-shared-upgrade', active=True
        )
        owner = Client.objects.create(
            name='Synthetic preserved source', code='shared-upgrade'
        )
        machine = AssetMachine.objects.create(
            name='Synthetic old placement', client=owner, placement_version=7
        )
        state_fields = (
            'id',
            'namespace_id',
            'epoch',
            'entity_kind',
            'aspect',
            'source_id',
            'revision',
            'client_id',
            'location_id',
            'native_version',
            'deleted',
        )
        event_fields = (
            'id',
            'namespace_id',
            'epoch',
            'entity_kind',
            'aspect',
            'source_id',
            'revision',
            'operation',
            'previous',
            'current',
            'captured_at',
        )
        before_states = list(
            old_apps.get_model('integration_state', 'SourceState').objects.values(
                *state_fields
            )
        )
        before_events = list(
            old_apps.get_model('integration_state', 'SourceChange').objects.values(
                *event_fields
            )
        )
        self.assertEqual(len(before_states), 1)
        self.assertEqual(before_states[0]['native_version'], 7)
        try:
            MigrationExecutor(connection).migrate([final])
            self.assertEqual(
                list(SourceState.objects.values(*state_fields)), before_states
            )
            self.assertEqual(
                list(SourceChange.objects.values(*event_fields)), before_events
            )
            machine.refresh_from_db()
            self.assertEqual(machine.client_id, owner.pk)
            self.assertEqual(machine.placement_version, 7)
            self.assertIsNone(SourceState.objects.get().generic_identity_id)
            current = SourceNamespace.objects.get(pk=namespace.pk)
            key = SharedSourceKey(
                namespace_id=current.pk,
                environment=current.environment,
                epoch=current.epoch,
                entity_kind='synthetic_document',
                aspect='metadata',
                source_id='private-7',
            )
            created = record_observation(
                key,
                event_key='native-retained',
                expected_fence=0,
                observation=SourceObservation(state='available'),
            )
            with self.assertRaises(IrreversibleError):
                MigrationExecutor(connection).migrate([
                    ('integration_state', '0002_machine_placement_capture')
                ])
            self.assertTrue(SourceChange.objects.filter(pk=created.change_id).exists())
            self.assertTrue(
                AssetMachine.objects.filter(pk=machine.pk, placement_version=7).exists()
            )
        finally:
            MigrationExecutor(connection).migrate([final])
            SourceNamespace.objects.update(active=False)

    def test_actual_delivery_backout_refuses_retained_consumer_and_preserves_history(
        self,
    ):
        """A real executor refuses before dropping any configured-consumer schema."""
        from uuid import uuid4

        from django.db.migrations.recorder import MigrationRecorder

        from integration_state.models import (
            EventConsumer,
            GenericSourceIdentity,
            SourceNamespace,
        )
        from integration_state.shared_delivery import configure_consumer

        namespace = SourceNamespace.objects.create(
            environment='synthetic-native-delivery', active=True
        )
        consumer = configure_consumer(
            namespace.pk,
            'retained-native-consumer',
            aspects=(('machine', 'placement'),),
            configuration_epoch=uuid4(),
        )
        self.assertFalse(GenericSourceIdentity.objects.exists())
        before = set(MigrationRecorder(connection).applied_migrations())
        with self.assertRaises(IrreversibleError):
            MigrationExecutor(connection).migrate([
                ('integration_state', '0003_generic_source_events')
            ])
        self.assertEqual(
            set(MigrationRecorder(connection).applied_migrations()), before
        )
        self.assertTrue(
            EventConsumer.objects.filter(pk=consumer.consumer_id, active=True).exists()
        )
        SourceNamespace.objects.update(active=False)

    def test_actual_scan_backout_refuses_retained_request_and_preserves_history(self):
        """The native reverse guard runs before dropping retained scan tables."""
        from django.db.migrations.recorder import MigrationRecorder

        from integration_state.models import ReconciliationScan, SourceNamespace
        from integration_state.s7_reconciliation import ScanBinding
        from integration_state.shared_reconciliation import (
            begin_scan,
            reserve_scan_page,
        )

        namespace = SourceNamespace.objects.create(
            environment='synthetic-native-scan', active=True
        )
        binding = ScanBinding(
            environment=namespace.environment,
            epoch=namespace.epoch,
            entity_kind='synthetic_document',
            aspect='metadata',
            actor_key='synthetic-owner',
            client_id=7,
            filter_key='retained-filter',
            generation='fixture-generation',
        )
        initial = begin_scan(
            namespace.pk,
            'retained-native-scan',
            binding,
            max_pages=2,
            max_records=5,
            authorize=lambda value: True,
        )
        pending = reserve_scan_page(initial, authorize=lambda value: True)
        before = set(MigrationRecorder(connection).applied_migrations())
        with self.assertRaises(IrreversibleError):
            MigrationExecutor(connection).migrate([
                ('integration_state', '0004_event_delivery')
            ])
        self.assertEqual(
            set(MigrationRecorder(connection).applied_migrations()), before
        )
        self.assertEqual(
            ReconciliationScan.objects.get(pk=initial.scan_id).version, pending.version
        )
        SourceNamespace.objects.update(active=False)
