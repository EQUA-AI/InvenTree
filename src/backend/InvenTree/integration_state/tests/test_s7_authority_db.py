"""S7 adapters composed with actual synthetic placement and same-ledger rows.

These are ORM/profile controls, not authenticated HTTP, PostgreSQL concurrency,
S7 persistence, historical authority, all-writer or production deletion proof.
"""

from integration_state.tests.assertions import assert_literal_bool

from dataclasses import replace
from typing import cast
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings

from tasks.scope import scoped_machines

from ai.core.tests.test_integration_s6_history_boundary_db import S6AIRequestFixture
from assets.integration_authority import explicitly_granted_client_ids
from assets.integration_contracts import ClientSourceIdentity
from assets.integration_machine_location import read_machine_placement
from assets.models import (
    AssetMachine,
    Client,
    ClientScopeGrant,
    MachineLocationTransfer,
    MachinePlacementHistory,
)
from assets.tests.integration_s5_test_support import NativePlacementFixture
from integration_state.models import (
    GenericSourceIdentity,
    SourceChange,
    SourceNamespace,
    SourceState,
)
from integration_state.s7_changes import (
    PLACEMENT_CHANGE_FIELDS,
    assess_placement_change,
    assess_shared_change,
    invalidation_for_client,
    placement_change_from_values,
    placement_state_from_expectation,
    shared_change_from_record,
)
from integration_state.services import PlacementStateError, current_expectation
from integration_state.shared_contracts import SharedSourceKey, SourceObservation
from integration_state.shared_events import (
    SharedConflictError,
    change_record,
    record_observation,
)
from users.models import RuleSet


class NativePlacementContext(NativePlacementFixture):
    """Share native fixture helpers without inheriting another testcase class."""

    def setUp(self):
        """Activate capture only after actual synthetic native fixture creation."""
        super().setUp()
        self.namespace = SourceNamespace.objects.create(
            environment='synthetic-s5', active=True
        )

    def changes(self):
        """Read the real declared ORM values projection, without fabricating fields."""
        return [
            placement_change_from_values(
                environment=self.namespace.environment,
                namespace_id=self.namespace.pk,
                epoch=self.namespace.epoch,
                record=row,
            )
            for row in SourceChange.objects
            .filter(
                namespace=self.namespace,
                epoch=self.namespace.epoch,
                source_id=self.machine.pk,
            )
            .order_by('revision')
            .values(*PLACEMENT_CHANGE_FIELDS)
        ]


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class NativePlacementCompositionTests(NativePlacementContext, TestCase):
    """Use unchanged native writes, SQL capture and S5 services before S7."""

    def test_native_transfer_projects_real_event_and_current_expectation(self):
        """A real native receipt, marker and S7 observation describe one placement."""
        native = self.move()
        (change,) = self.changes()
        observation = placement_state_from_expectation(
            current_expectation(self.namespace.pk, self.machine.pk)
        )
        self.assertFalse(native['replayed'])
        self.assertEqual(change.identity, observation.identity)
        self.assertEqual(change.revision, observation.revision)
        self.assertEqual(change.current, observation.placement)
        self.assertEqual(change.current.client_id, self.owner.pk)
        self.assertEqual(change.current.location_id, self.destination.pk)
        self.assertEqual(change.current.native_version, 1)
        assessment = assess_placement_change(None, change)
        self.assertEqual(assessment.state, 'accepted')
        self.assertEqual(assessment.checkpoint, change)
        self.assertEqual(change.tombstones, ())
        self.assertFalse(change.removal_complete)
        self.assertFalse(change.deletion_applied)
        self.assertEqual(SourceChange.objects.count(), 1)
        self.assertEqual(SourceState.objects.count(), 1)
        self.assertEqual(MachineLocationTransfer.objects.count(), 1)
        self.assertEqual(MachinePlacementHistory.objects.count(), 1)

    def test_native_replay_and_later_move_do_not_regress_s7_checkpoint(self):
        """Actual old native receipt replay creates no event or checkpoint advance."""
        key = uuid4()
        initial = self.move(key=key)
        (first,) = self.changes()
        replay = self.move(version=0, key=key)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['transfer_id'], initial['transfer_id'])
        self.assertEqual(SourceChange.objects.count(), 1)
        self.assertEqual(
            assess_placement_change(first, self.changes()[0]).state, 'duplicate'
        )
        self.move(destination=None)
        first_again, second = self.changes()
        self.assertEqual(first_again, first)
        self.assertEqual(assess_placement_change(first, second).state, 'accepted')
        replay = self.move(version=0, key=key)
        self.assertTrue(replay['replayed'])
        stale = assess_placement_change(second, self.changes()[0])
        self.assertEqual(stale.state, 'stale')
        self.assertEqual(stale.checkpoint, second)
        self.assertEqual(SourceState.objects.get().revision, 2)
        self.assertEqual(SourceChange.objects.count(), 2)
        self.assertEqual(MachineLocationTransfer.objects.count(), 2)
        self.assertEqual(MachinePlacementHistory.objects.count(), 2)

    def test_real_bulk_aba_advances_s7_without_inventing_native_version(self):
        """Two genuine alternate writes return to the same tuple with newer fences."""
        self.move()
        AssetMachine.objects.filter(pk=self.machine.pk).update(physical_location=None)
        AssetMachine.objects.filter(pk=self.machine.pk).update(
            physical_location=self.destination
        )
        first, middle, last = self.changes()
        self.assertEqual(first.current, last.current)
        self.assertEqual((first.revision, middle.revision, last.revision), (1, 2, 3))
        self.assertEqual(
            tuple(change.current.native_version for change in (first, middle, last)),
            (1, 1, 1),
        )
        self.assertEqual(assess_placement_change(first, middle).state, 'accepted')
        self.assertEqual(assess_placement_change(middle, last).state, 'accepted')
        observation = placement_state_from_expectation(
            current_expectation(self.namespace.pk, self.machine.pk)
        )
        self.assertEqual(observation.revision, 3)
        self.assertEqual(observation.placement, last.current)
        self.assertEqual(MachineLocationTransfer.objects.count(), 1)
        self.assertEqual(MachinePlacementHistory.objects.count(), 1)

    def test_native_transaction_rollback_leaves_no_change_to_publish(self):
        """A nested test transaction rollback removes native receipt and capture."""
        with self.assertRaisesRegex(RuntimeError, 'synthetic S7 rollback'):
            with transaction.atomic():
                self.move()
                self.assertEqual(len(self.changes()), 1)
                raise RuntimeError('synthetic S7 rollback')
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.placement_version, 0)
        self.assertIsNone(self.machine.physical_location_id)
        self.assertEqual(self.changes(), [])
        self.assertFalse(SourceState.objects.exists())
        self.assertFalse(MachineLocationTransfer.objects.exists())
        self.assertFalse(MachinePlacementHistory.objects.exists())

    def test_actual_synthetic_source_delete_is_not_downstream_erasure(self):
        """The real retained deletion row stays private and non-erasure evidence."""
        machine_pk = self.machine.pk
        self.machine.delete()
        self.machine.pk = machine_pk
        (change,) = self.changes()
        self.assertFalse(AssetMachine.objects.filter(pk=machine_pk).exists())
        self.assertTrue(SourceState.objects.get().deleted)
        self.assertEqual(change.operation, 'deleted')
        self.assertEqual(change.previous.client_id, self.owner.pk)
        self.assertIsNone(change.current)
        self.assertEqual(assess_placement_change(None, change).state, 'accepted')
        self.assertEqual(change.tombstones, ())
        self.assertFalse(change.removal_complete)
        self.assertFalse(change.deletion_applied)
        with self.assertRaises(PlacementStateError):
            current_expectation(self.namespace.pk, machine_pk)

    def test_actual_native_creation_maps_nullable_placement_and_zero_version(self):
        """An enrolled native INSERT supplies an actual created row, not a fixture."""
        self.machine = AssetMachine.objects.create(
            name='Synthetic S7 created source', client=self.owner
        )
        (change,) = self.changes()
        self.assertEqual(change.operation, 'created')
        self.assertEqual(change.identity.source_id, self.machine.pk)
        self.assertEqual(change.identity.epoch, self.namespace.epoch)
        self.assertIsNone(change.previous)
        self.assertIsNone(change.current.location_id)
        self.assertEqual(change.current.native_version, 0)
        self.assertEqual(assess_placement_change(None, change).state, 'accepted')

    def test_nonplacement_native_edit_remains_explicitly_outside_capture(self):
        """Placement evidence must not masquerade as complete machine metadata CDC."""
        self.move()
        (before,) = self.changes()
        AssetMachine.objects.filter(pk=self.machine.pk).update(
            name='Synthetic independently renamed source'
        )
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.name, 'Synthetic independently renamed source')
        (after,) = self.changes()
        self.assertEqual(after, before)
        self.assertEqual(assess_placement_change(before, after).state, 'duplicate')
        self.assertEqual(SourceState.objects.get().revision, 1)
        self.assertEqual(SourceChange.objects.count(), 1)


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='s5-owner',
)
class NativeInvalidationAuthorityTests(NativePlacementContext, TestCase):
    """Compose S7 with actual native role, scope and independent grant readers.

    The callback is test composition, not an installed production authorizer.
    No authenticated transport, historical scope or atomic recheck is claimed.
    """

    def setUp(self):
        """An ordinary reader has a real role and an independent explicit grant."""
        super().setUp()
        self.move()
        (self.change,) = self.changes()
        self.reader = get_user_model().objects.create_user(username='s7-native-reader')
        self.group = Group.objects.create(name='Synthetic S7 native readers')
        self.reader.groups.add(self.group)
        RuleSet.objects.update_or_create(
            group=self.group, name='work_order', defaults={'can_view': True}
        )
        ClientScopeGrant.objects.create(user=self.reader, client=self.owner)
        self.other = Client.objects.create(name='Synthetic S7 other', code='s7-other')
        self.client = ClientSourceIdentity(self.namespace.environment, self.owner.pk)

    def native_authorize(self, identity, client):
        """Delegate to the existing native reader, then match the current client.

        This intentionally supplies no old-owner historical disclosure policy.
        Its server environment and ordinary actor are synthetic fixture inputs.
        """
        edge = read_machine_placement(
            self.reader,
            environment=self.namespace.environment,
            machine_pk=identity.source_id,
        )
        return bool(
            edge is not None
            and edge.machine.environment == identity.environment == client.environment
            and AssetMachine.objects.filter(
                pk=identity.source_id, client_id=client.pk
            ).exists()
        )

    def notice(self):
        """Invoke the actual S7 two-check private projection."""
        return invalidation_for_client(
            self.change, self.client, authorize=self.native_authorize
        )

    def assert_native_source_unchanged(self):
        """Scope/refusal reads cannot manufacture another source or native effect."""
        self.assertEqual(self.changes(), [self.change])
        self.assertEqual(SourceState.objects.get().revision, 1)
        self.assertEqual(MachineLocationTransfer.objects.count(), 1)
        self.assertEqual(MachinePlacementHistory.objects.count(), 1)

    def test_current_native_reader_supports_minimal_s7_refresh(self):
        """A useful ordinary-role positive carries no captured destination tuple."""
        self.assertFalse(self.reader.is_superuser)
        self.assertTrue(
            scoped_machines(self.reader).filter(pk=self.machine.pk).exists()
        )
        self.assertTrue(
            explicitly_granted_client_ids(self.reader)
            .filter(client_id=self.owner.pk)
            .exists()
        )
        notice = self.notice()
        self.assertEqual(notice.reason, 'refresh')
        self.assertEqual(notice.client, self.client)
        self.assertEqual(notice.identity, self.change.identity)
        self.assertEqual(
            set(vars(notice)), {'identity', 'client', 'revision', 'reason'}
        )
        self.assert_native_source_unchanged()

    def test_missing_integration_grant_denied_while_native_scope_positive(self):
        """The genuine single-site native positive survives grant removal."""
        self.assertIsNotNone(self.notice())
        ClientScopeGrant.objects.filter(user=self.reader, client=self.owner).delete()
        try:
            self.assertTrue(
                scoped_machines(self.reader).filter(pk=self.machine.pk).exists()
            )
            self.assertFalse(explicitly_granted_client_ids(self.reader).exists())
            with self.assertRaises(PermissionError):
                self.notice()
        finally:
            ClientScopeGrant.objects.create(user=self.reader, client=self.owner)
        self.assertEqual(self.notice().reason, 'refresh')
        self.assert_native_source_unchanged()

    def test_native_scope_denied_while_integration_grant_retained(self):
        """Changing real native resolver configuration does not remove the grant."""
        self.assertIsNotNone(self.notice())
        with self.settings(AIMMS_SINGLE_SITE_CLIENT_CODE=self.other.code):
            self.assertTrue(
                explicitly_granted_client_ids(self.reader)
                .filter(client_id=self.owner.pk)
                .exists()
            )
            self.assertFalse(
                scoped_machines(self.reader).filter(pk=self.machine.pk).exists()
            )
            with self.assertRaises(PermissionError):
                self.notice()
        self.assertEqual(self.notice().reason, 'refresh')
        self.assert_native_source_unchanged()

    def test_role_revocation_uses_ordinary_reader_and_restores(self):
        """A cached actor cannot keep a revoked stored native Work view role."""
        self.assertFalse(self.reader.is_superuser)
        self.assertIsNotNone(self.notice())
        role = RuleSet.objects.filter(group=self.group, name='work_order')
        role.update(can_view=False)
        try:
            self.assertTrue(
                explicitly_granted_client_ids(self.reader)
                .filter(client_id=self.owner.pk)
                .exists()
            )
            with self.assertRaises(PermissionError):
                self.notice()
        finally:
            role.update(can_view=True)
        self.assertEqual(self.notice().reason, 'refresh')
        self.assert_native_source_unchanged()

    def test_account_deactivation_refuses_cached_actor(self):
        """Fresh native account lookup wins over the still-active Python instance."""
        self.assertIsNotNone(self.notice())
        get_user_model().objects.filter(pk=self.reader.pk).update(is_active=False)
        try:
            self.assertTrue(self.reader.is_active)
            with self.assertRaises(PermissionError):
                self.notice()
        finally:
            get_user_model().objects.filter(pk=self.reader.pk).update(is_active=True)
        self.assertEqual(self.notice().reason, 'refresh')
        self.assert_native_source_unchanged()

    def test_grant_revocation_between_s7_checks_withholds_notice(self):
        """An actual DB grant mutation after the first allowed check fences output."""
        self.assertIsNotNone(self.notice())
        observed = []

        def revoke_after_first(identity, client):
            allowed = self.native_authorize(identity, client)
            observed.append(allowed)
            if len(observed) == 1:
                ClientScopeGrant.objects.filter(
                    user=self.reader, client=self.owner
                ).delete()
            return allowed

        try:
            with self.assertRaises(PermissionError):
                invalidation_for_client(
                    self.change, self.client, authorize=revoke_after_first
                )
            self.assertEqual(observed, [True, False])
        finally:
            ClientScopeGrant.objects.create(user=self.reader, client=self.owner)
        self.assertEqual(self.notice().reason, 'refresh')
        self.assert_native_source_unchanged()

    def test_transfer_out_does_not_grant_old_owner_historical_notice(self):
        """Current native access denies the old owner; no history is invented."""
        self.assertIsNotNone(self.notice())
        AssetMachine.objects.filter(pk=self.machine.pk).update(
            client=self.other, physical_location=None
        )
        first, moved = self.changes()
        self.assertEqual(first, self.change)
        self.assertEqual(assess_placement_change(first, moved).state, 'accepted')
        self.assertEqual(moved.previous.client_id, self.owner.pk)
        self.assertEqual(moved.current.client_id, self.other.pk)
        with self.assertRaises(PermissionError):
            invalidation_for_client(moved, self.client, authorize=self.native_authorize)
        ClientScopeGrant.objects.create(user=self.reader, client=self.other)
        other_identity = ClientSourceIdentity(self.namespace.environment, self.other.pk)
        with self.settings(AIMMS_SINGLE_SITE_CLIENT_CODE=self.other.code):
            notice = invalidation_for_client(
                moved, other_identity, authorize=self.native_authorize
            )
        self.assertEqual(notice.reason, 'refresh')
        self.assertEqual(notice.client, other_identity)
        self.assertEqual(
            set(vars(notice)), {'identity', 'client', 'revision', 'reason'}
        )
        self.assertEqual(SourceChange.objects.count(), 2)
        self.assertEqual(SourceState.objects.get().revision, 2)
        self.assertEqual(MachineLocationTransfer.objects.count(), 1)

    def test_other_client_grant_does_not_authorize_source_owner(self):
        """Native scope stays positive while only an unrelated explicit grant exists."""
        self.assertIsNotNone(self.notice())
        ClientScopeGrant.objects.filter(user=self.reader, client=self.owner).delete()
        ClientScopeGrant.objects.create(user=self.reader, client=self.other)
        self.assertTrue(
            scoped_machines(self.reader).filter(pk=self.machine.pk).exists()
        )
        self.assertTrue(
            explicitly_granted_client_ids(self.reader)
            .filter(client_id=self.other.pk)
            .exists()
        )
        with self.assertRaises(PermissionError):
            self.notice()
        self.assert_native_source_unchanged()


class SharedLedgerConsumerTests(TransactionTestCase):
    """Read real S5 metadata storage, not native document or actor authority."""

    def setUp(self):
        """Only a synthetic private namespace is needed, not business fixtures."""
        super().setUp()
        self.namespace = SourceNamespace.objects.create(
            environment='synthetic-s7-shared', active=True
        )

    def tearDown(self):
        """Disable only this owned namespace before native fixture flush."""
        try:
            SourceNamespace.objects.filter(pk=self.namespace.pk).update(active=False)
        finally:
            super().tearDown()

    def key(self, source_id):
        """Use literal synthetic keys without claiming a native incarnation."""
        return SharedSourceKey(
            namespace_id=self.namespace.pk,
            environment=self.namespace.environment,
            epoch=self.namespace.epoch,
            entity_kind='synthetic_document',
            aspect='metadata',
            source_id=source_id,
            declared_incarnation=None,
        )

    def parse(self, record):
        """Bind to independently created namespace truth, not the DTO's key."""
        return shared_change_from_record(
            environment=self.namespace.environment,
            namespace_id=self.namespace.pk,
            epoch=self.namespace.epoch,
            record=record,
        )

    def test_same_ledger_rows_retain_typed_keys_and_unknown_revision(self):
        """Actual registry/state/event round trips do not invent native proof."""
        self.assertFalse(connection.in_atomic_block)
        self.assertTrue(connection.get_autocommit())
        keys = [self.key(7), self.key('7'), self.key('007')]
        keys.append(replace(keys[0], declared_incarnation='operator-claim-only'))
        observation = SourceObservation(state='available', source_revision=None)
        recorded = []
        for key in keys:
            source = record_observation(
                key, event_key=str(uuid4()), expected_fence=0, observation=observation
            )
            row = SourceChange.objects.select_related('generic_identity').get(
                pk=source.change_id
            )
            actual = change_record(row, row.generic_identity, self.namespace)
            self.assertEqual(actual, source)
            parsed = self.parse(actual)
            self.assertEqual(parsed.key, key)
            self.assertIs(type(parsed.key.source_id), type(key.source_id))
            self.assertIsNone(parsed.observation.source_revision)
            self.assertIsNone(parsed.observation.client_ids)
            self.assertEqual(parsed.key.declared_incarnation, key.declared_incarnation)
            self.assertEqual(assess_shared_change(None, parsed).state, 'accepted')
            self.assertFalse(parsed.native_authorized)
            self.assertFalse(parsed.source_population_complete)
            self.assertFalse(parsed.source_deleted)
            self.assertFalse(parsed.deletion_applied)
            self.assertEqual(row.operation, 'observed')
            self.assertIsNone(row.source_id)
            recorded.append(parsed)
        self.assertEqual(len({result.event_id for result in recorded}), len(keys))
        self.assertEqual(len({result.key for result in recorded}), len(keys))
        self.assertEqual(GenericSourceIdentity.objects.count(), len(keys))
        self.assertEqual(SourceState.objects.count(), len(keys))
        self.assertEqual(SourceChange.objects.count(), len(keys))
        for marker in SourceState.objects.all():
            self.assertIsNotNone(marker.generic_identity_id)
            self.assertIsNone(marker.source_id)
            self.assertIsNone(marker.native_version)
            self.assertFalse(marker.deleted)
        self.assertFalse(connection.in_atomic_block)

    def test_actual_replay_keeps_checkpoint_and_missing_is_not_delete(self):
        """Same-key native replay returns its old event without S7 regression."""
        key = self.key('private-replay')
        available = SourceObservation(state='available', source_revision=None)
        first = record_observation(
            key, event_key='first', expected_fence=0, observation=available
        )
        second = record_observation(
            key,
            event_key='later',
            expected_fence=1,
            observation=SourceObservation(state='missing', source_revision=None),
        )
        replay = record_observation(
            key, event_key='first', expected_fence=0, observation=available
        )
        self.assertEqual(replay, first)
        old, current, repeated = map(self.parse, (first, second, replay))
        self.assertEqual(assess_shared_change(old, repeated).state, 'duplicate')
        self.assertEqual(assess_shared_change(old, current).state, 'accepted')
        stale = assess_shared_change(current, repeated)
        self.assertEqual(stale.state, 'stale')
        self.assertIs(stale.checkpoint, current)
        marker = SourceState.objects.get()
        self.assertEqual(marker.revision, current.fence)
        self.assertEqual(marker.generic_snapshot['state'], 'missing')
        self.assertFalse(marker.deleted)
        self.assertFalse(current.source_deleted)
        self.assertFalse(current.deletion_applied)
        with self.assertRaises(SharedConflictError):
            record_observation(
                key, event_key='stale-new-key', expected_fence=0, observation=available
            )
        self.assertEqual(SourceChange.objects.count(), 2)
        self.assertEqual(SourceState.objects.get().revision, current.fence)

    def test_outer_rollback_does_not_make_provisional_dto_commit_proof(self):
        """A private accepted DTO does not certify its caller's outer commit."""
        key = self.key('private-rollback')
        first = record_observation(
            key,
            event_key='committed',
            expected_fence=0,
            observation=SourceObservation(state='available', source_revision=None),
        )
        checkpoint = self.parse(first)
        with self.assertRaisesRegex(RuntimeError, 'synthetic outer rollback'):
            with transaction.atomic():
                provisional = record_observation(
                    key,
                    event_key='provisional',
                    expected_fence=1,
                    observation=SourceObservation(
                        state='missing', source_revision=None
                    ),
                )
                parsed = self.parse(provisional)
                self.assertEqual(
                    assess_shared_change(checkpoint, parsed).state, 'accepted'
                )
                self.assertFalse(parsed.native_authorized)
                self.assertTrue(connection.in_atomic_block)
                self.assertEqual(SourceChange.objects.count(), 2)
                raise RuntimeError('synthetic outer rollback')
        self.assertFalse(connection.in_atomic_block)
        self.assertFalse(SourceChange.objects.filter(pk=provisional.change_id).exists())
        self.assertEqual(SourceChange.objects.count(), 1)
        self.assertEqual(GenericSourceIdentity.objects.count(), 1)
        marker = SourceState.objects.get()
        self.assertEqual(marker.revision, first.fence)
        self.assertEqual(marker.generic_snapshot['state'], 'available')
        self.assertFalse(marker.deleted)
        row = SourceChange.objects.select_related('generic_identity').get(
            pk=first.change_id
        )
        retained = self.parse(change_record(row, row.generic_identity, self.namespace))
        self.assertEqual(retained, checkpoint)
        self.assertEqual(assess_shared_change(checkpoint, retained).state, 'duplicate')


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class NativeDeliveredChangeTests(NativePlacementContext, TransactionTestCase):
    """Committed real S5 leases compose with S7; no transport/effect is installed."""

    def configure(self, *, key='private-s7-delivery'):  # gitleaks:allow -- Non-secret synthetic consumer identifier.
        """Configure actual native and generic aspects, not an actor grant."""
        from integration_state.shared_delivery import configure_consumer

        return configure_consumer(
            self.namespace.pk,
            key,
            aspects=(('machine', 'placement'), ('synthetic_document', 'metadata')),
            configuration_epoch=uuid4(),
        )

    def claim(self, configuration, *, worker='private-s7-worker'):
        """Actual S5 absence-based selection supplies its committed envelopes."""
        from integration_state.shared_delivery import claim_changes

        return claim_changes(
            configuration.consumer_id, worker_key=worker, limit=10, lease_seconds=300
        )

    def decode(self, configuration, claim):
        """Keep independently selected server environment and worker binding."""
        from integration_state.s7_changes import delivery_change_from_claim

        return delivery_change_from_claim(
            environment=self.namespace.environment,
            worker_key='private-s7-worker',
            configuration=configuration,
            claim=claim,
        )

    def test_real_native_and_generic_claims_decode_and_ack_without_source_effect(self):
        """Actual producer bytes preserve both branches and original capture time."""
        from integration_state.models import ConsumerAspectProgress, EventDelivery
        from integration_state.shared_delivery import ack_change

        self.assertFalse(connection.in_atomic_block)
        self.move()
        (native,) = self.changes()
        key = SharedSourceKey(
            namespace_id=self.namespace.pk,
            environment=self.namespace.environment,
            epoch=self.namespace.epoch,
            entity_kind='synthetic_document',
            aspect='metadata',
            source_id=str(self.machine.pk),
        )
        recorded = record_observation(
            key,
            event_key='private-delivery-observation',
            expected_fence=0,
            observation=SourceObservation(state='available', source_revision=None),
        )
        generic = shared_change_from_record(
            environment=self.namespace.environment,
            namespace_id=self.namespace.pk,
            epoch=self.namespace.epoch,
            record=recorded,
        )
        expected = {native.event_id: native, generic.event_id: generic}
        configuration = self.configure()
        claims = self.claim(configuration)
        self.assertEqual({value.change_id for value in claims}, set(expected))
        self.assertEqual(len(claims), 2)
        self.assertFalse(connection.in_atomic_block)
        for claim in claims:
            result = self.decode(configuration, claim)
            self.assertEqual(result.change, expected[claim.change_id])
            self.assertEqual(
                result.captured_at.isoformat(),
                SourceChange.objects.get(pk=claim.change_id).captured_at.isoformat(),
            )
            self.assertFalse(result.ownership_verified)
            self.assertFalse(result.native_authorized)
            self.assertFalse(result.deletion_applied)
            receipt = ack_change(result.claim, outcome='applied')
            self.assertTrue(receipt.advanced)
            self.assertEqual(ack_change(result.claim, outcome='applied'), receipt)
            self.assertFalse(receipt.erasure_complete)
        self.assertEqual(self.claim(configuration), ())
        self.assertEqual(ConsumerAspectProgress.objects.count(), 2)
        self.assertEqual(EventDelivery.objects.filter(state='acknowledged').count(), 2)
        self.assertEqual(SourceChange.objects.count(), 2)
        self.assertEqual(SourceState.objects.count(), 2)
        self.assertEqual(GenericSourceIdentity.objects.count(), 1)
        self.assertEqual(MachineLocationTransfer.objects.count(), 1)
        self.assertEqual(MachinePlacementHistory.objects.count(), 1)

    def test_metadata_decode_does_not_adopt_an_expired_replaced_lease(self):
        """Only actual S5 current ownership can acknowledge a detached old claim."""
        from datetime import timedelta

        from django.utils import timezone

        from integration_state.models import ConsumerAspectProgress, EventDelivery
        from integration_state.shared_delivery import ack_change

        self.move()
        configuration = self.configure()
        (old,) = self.claim(configuration)
        EventDelivery.objects.filter(pk=old.delivery_id).update(
            lease_until=timezone.now() - timedelta(seconds=1)
        )
        (current,) = self.claim(configuration)
        self.assertEqual(current.change_id, old.change_id)
        self.assertGreater(current.ownership_fence, old.ownership_fence)
        self.assertNotEqual(current.lease_token, old.lease_token)
        decoded_old = self.decode(configuration, old)
        decoded_current = self.decode(configuration, current)
        self.assertEqual(decoded_old.change, decoded_current.change)
        self.assertFalse(decoded_old.ownership_verified)
        with self.assertRaises(SharedConflictError):
            ack_change(decoded_old.claim, outcome='applied')
        receipt = ack_change(decoded_current.claim, outcome='uncertain')
        self.assertEqual(receipt.outcome, 'uncertain')
        self.assertFalse(receipt.advanced)
        self.assertFalse(ConsumerAspectProgress.objects.exists())
        self.assertEqual(self.claim(configuration), ())
        self.assertEqual(EventDelivery.objects.filter(state='expired').count(), 1)
        self.assertEqual(SourceChange.objects.count(), 1)
        self.assertEqual(MachineLocationTransfer.objects.count(), 1)

    def test_rotated_configuration_requires_current_s5_ack_admission(self):
        """Coherent old metadata cannot authorize a changed consumer configuration."""
        from integration_state.shared_delivery import ack_change
        from integration_state.shared_events import SharedStateError

        self.move()
        old_configuration = self.configure()
        (old,) = self.claim(old_configuration)
        current_configuration = self.configure()
        self.assertNotEqual(
            old_configuration.configuration_epoch,
            current_configuration.configuration_epoch,
        )
        self.assertFalse(self.decode(old_configuration, old).ownership_verified)
        with self.assertRaises(ValueError):
            self.decode(current_configuration, old)
        with self.assertRaises(SharedStateError):
            ack_change(old, outcome='applied')
        (current,) = self.claim(current_configuration)
        decoded = self.decode(current_configuration, current)
        self.assertEqual(decoded.change.event_id, old.change_id)
        self.assertTrue(ack_change(decoded.claim, outcome='applied').advanced)
        self.assertEqual(SourceChange.objects.count(), 1)
        self.assertEqual(MachineLocationTransfer.objects.count(), 1)


class NativePlanningContext(NativePlacementContext):
    """Current native read eligibility only, never deletion/retention approval."""

    def setUp(self):
        """Keep real ordinary role, single-site scope and explicit grant separate."""
        from integration_state.s7_contracts import SourceAspectIdentity
        from integration_state.s7_reconciliation import ScanBinding

        super().setUp()
        self.move()
        self.reader = get_user_model().objects.create_user(
            username='s7-planning-reader'
        )
        self.group = Group.objects.create(name='Synthetic S7 planning readers')
        self.reader.groups.add(self.group)
        RuleSet.objects.update_or_create(
            group=self.group, name='work_order', defaults={'can_view': True}
        )
        ClientScopeGrant.objects.create(user=self.reader, client=self.owner)
        self.source = SourceAspectIdentity(
            environment=self.namespace.environment,
            epoch=self.namespace.epoch,
            entity_kind='machine',
            aspect='placement',
            source_id=self.machine.pk,
        )
        self.client_identity = ClientSourceIdentity(
            self.namespace.environment, self.owner.pk
        )
        self.actor_key = f'synthetic-native-user:{self.reader.pk}'
        self.binding = ScanBinding(
            environment=self.namespace.environment,
            epoch=self.namespace.epoch,
            entity_kind='machine',
            aspect='placement',
            actor_key=self.actor_key,
            client_id=self.owner.pk,
            filter_key=f'only-fixture-machine:{self.machine.pk}',
            generation='fixture-label-not-source-completeness',
        )
        self.callback_depths = []

    def read_eligible(self):
        """Call the unchanged current native reader outside metadata transactions."""
        self.callback_depths.append(len(connection.atomic_blocks))
        edge = read_machine_placement(
            self.reader,
            environment=self.namespace.environment,
            machine_pk=self.machine.pk,
        )
        return bool(
            edge is not None
            and edge.machine.environment == self.namespace.environment
            and AssetMachine.objects.filter(
                pk=self.machine.pk, client=self.owner
            ).exists()
        )

    def scan_read_eligible(self, binding):
        """Bind this exact fixture subset before evaluating actual current access."""
        return binding == self.binding and self.read_eligible()

    def native_page(self):
        """Derive a nonempty selected-subset page from the actual native queryset."""
        from integration_state.s7_contracts import SourceAspectIdentity
        from integration_state.s7_reconciliation import ScanPage

        keys = tuple(
            scoped_machines(self.reader)
            .filter(pk=self.machine.pk)
            .order_by('pk')
            .values_list('pk', flat=True)
        )
        case = cast(TransactionTestCase, self)
        case.assertEqual(keys, (self.machine.pk,))
        items = tuple(replace(self.source, source_id=key) for key in keys)
        case.assertTrue(all(type(value) is SourceAspectIdentity for value in items))
        return ScanPage(
            binding=self.binding,
            offset=0,
            total=len(keys),
            items=items,
            next_offset=None,
        )

    def assert_native_untouched(self):
        """Metadata operations cannot turn planning reads into a native effect."""
        case = cast(TransactionTestCase, self)
        case.assertTrue(
            AssetMachine.objects.filter(pk=self.machine.pk, client=self.owner).exists()
        )
        case.assertEqual(SourceChange.objects.count(), 1)
        case.assertEqual(SourceState.objects.get().revision, 1)
        case.assertEqual(MachineLocationTransfer.objects.count(), 1)
        case.assertEqual(MachinePlacementHistory.objects.count(), 1)
        case.assertTrue(self.callback_depths)
        case.assertEqual(set(self.callback_depths), {0})


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='s5-owner',
)
class NativeDurableScanTests(NativePlanningContext, TransactionTestCase):
    """Adopt actual S5 persistence of S7 carriers, not a second repository."""

    def begin(self):
        """One fixed-budget request for the independently scoped native fixture."""
        from integration_state.shared_reconciliation import begin_scan

        self.assertFalse(connection.in_atomic_block)
        return begin_scan(
            self.namespace.pk,
            'private-native-scan',
            self.binding,
            max_pages=1,
            max_records=1,
            authorize=self.scan_read_eligible,
        )

    def test_real_native_subset_page_and_exact_replay_survive_s5_storage(self):
        """A useful persisted nonempty page proves transport, not population truth."""
        from integration_state.models import (
            ReconciliationAttempt,
            ReconciliationIdentity,
        )
        from integration_state.shared_reconciliation import (
            accept_scan_page,
            read_scan,
            reserve_scan_page,
        )

        initial = self.begin()
        self.assertEqual(self.begin(), initial)
        reserved = reserve_scan_page(initial, authorize=self.scan_read_eligible)
        self.assertEqual(reserved.progress.attempts_used, 1)
        self.assertFalse(connection.in_atomic_block)
        page = self.native_page()
        attempt = reserved.progress.pending_attempt
        completed = accept_scan_page(
            reserved, page, attempt=attempt, authorize=self.scan_read_eligible
        )
        self.assertEqual(completed.progress.state, 'transport_complete')
        self.assertEqual(completed.progress.seen, frozenset((self.source,)))
        self.assertFalse(completed.progress.population_complete)
        self.assertFalse(completed.progress.removal_complete)
        self.assertFalse(completed.progress.deletion_applied)
        self.assertEqual(completed.progress.tombstones, ())
        self.assertEqual(
            read_scan(
                initial.scan_id, binding=self.binding, authorize=self.scan_read_eligible
            ),
            completed,
        )
        self.assertEqual(
            accept_scan_page(
                reserved, page, attempt=attempt, authorize=self.scan_read_eligible
            ),
            completed,
        )
        self.assertEqual(ReconciliationAttempt.objects.count(), 1)
        self.assertEqual(ReconciliationAttempt.objects.get().state, 'accepted')
        self.assertEqual(ReconciliationIdentity.objects.count(), 1)
        self.assert_native_untouched()

    def test_grant_revocation_after_source_read_does_not_consume_reserved_result(self):
        """Native single-site scope stays positive while its independent grant is gone."""
        from integration_state.models import ReconciliationAttempt, ReconciliationScan
        from integration_state.shared_reconciliation import (
            accept_scan_page,
            read_scan,
            reserve_scan_page,
        )

        reserved = reserve_scan_page(self.begin(), authorize=self.scan_read_eligible)
        page = self.native_page()
        ClientScopeGrant.objects.filter(user=self.reader, client=self.owner).delete()
        try:
            self.assertTrue(
                scoped_machines(self.reader).filter(pk=self.machine.pk).exists()
            )
            self.assertFalse(explicitly_granted_client_ids(self.reader).exists())
            with self.assertRaises(PermissionError):
                accept_scan_page(
                    reserved,
                    page,
                    attempt=reserved.progress.pending_attempt,
                    authorize=self.scan_read_eligible,
                )
            with self.assertRaises(PermissionError):
                read_scan(
                    reserved.scan_id,
                    binding=self.binding,
                    authorize=self.scan_read_eligible,
                )
            self.assertEqual(ReconciliationScan.objects.get().version, reserved.version)
            self.assertEqual(ReconciliationAttempt.objects.get().state, 'pending')
        finally:
            ClientScopeGrant.objects.create(user=self.reader, client=self.owner)
        self.assertEqual(
            read_scan(
                reserved.scan_id,
                binding=self.binding,
                authorize=self.scan_read_eligible,
            ),
            reserved,
        )
        completed = accept_scan_page(
            reserved,
            page,
            attempt=reserved.progress.pending_attempt,
            authorize=self.scan_read_eligible,
        )
        self.assertEqual(completed.progress.state, 'transport_complete')
        self.assert_native_untouched()

    def test_role_revocation_after_committed_reservation_withholds_not_erases(self):
        """The second real role check fails after S5 already committed its reservation."""
        from integration_state.models import ReconciliationAttempt
        from integration_state.shared_reconciliation import read_scan, reserve_scan_page

        initial = self.begin()
        role = RuleSet.objects.filter(group=self.group, name='work_order')
        calls = []

        def revoke_then_read(binding):
            calls.append(True)
            if len(calls) == 2:
                role.update(can_view=False)
            return self.scan_read_eligible(binding)

        try:
            with self.assertRaises(PermissionError):
                reserve_scan_page(initial, authorize=revoke_then_read)
            self.assertEqual(len(calls), 2)
            self.assertTrue(
                explicitly_granted_client_ids(self.reader)
                .filter(client_id=self.owner.pk)
                .exists()
            )
            self.assertEqual(ReconciliationAttempt.objects.count(), 1)
            with self.assertRaises(PermissionError):
                read_scan(
                    initial.scan_id,
                    binding=self.binding,
                    authorize=self.scan_read_eligible,
                )
        finally:
            role.update(can_view=True)
        retained = read_scan(
            initial.scan_id, binding=self.binding, authorize=self.scan_read_eligible
        )
        self.assertEqual(retained.version, initial.version + 1)
        self.assertEqual(retained.progress.attempts_used, 1)
        self.assertIsNotNone(retained.progress.pending_attempt)
        self.assertEqual(ReconciliationAttempt.objects.get().state, 'pending')
        self.assert_native_untouched()

    def test_abandoned_native_read_preserves_fixed_attempt_budget(self):
        """Restore/retry cannot make the same lost reservation a fresh budget."""
        from integration_state.models import ReconciliationAttempt
        from integration_state.shared_reconciliation import (
            abandon_scan_page,
            read_scan,
            reserve_scan_page,
            retry_scan,
        )

        reserved = reserve_scan_page(self.begin(), authorize=self.scan_read_eligible)
        lost = abandon_scan_page(
            reserved,
            attempt=reserved.progress.pending_attempt,
            authorize=self.scan_read_eligible,
        )
        self.assertEqual(lost.progress.state, 'unavailable')
        exhausted = retry_scan(lost, authorize=self.scan_read_eligible)
        self.assertEqual(exhausted.progress.state, 'budget_exhausted')
        self.assertEqual(exhausted.progress.attempts_used, 1)
        self.assertEqual(exhausted.progress.max_pages, 1)
        self.assertEqual(exhausted.progress.max_records, 1)
        self.assertEqual(
            read_scan(
                exhausted.scan_id,
                binding=self.binding,
                authorize=self.scan_read_eligible,
            ),
            exhausted,
        )
        self.assertEqual(ReconciliationAttempt.objects.count(), 1)
        self.assertEqual(ReconciliationAttempt.objects.get().state, 'abandoned')
        self.assertEqual(exhausted.progress.tombstones, ())
        self.assert_native_untouched()


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='s5-owner',
)
class NativeDurablePlanningTests(NativePlanningContext, TransactionTestCase):
    """Private supplied copy claims survive S5 storage without native approval."""

    def intent(self):
        """Bind a private fixture intent, never a native delete command."""
        from integration_state.s7_deletion_requests import DeletionIntent

        return DeletionIntent(
            request_id=uuid4(),
            request_key='private-native-planning',
            request_revision=1,
            actor_key=self.actor_key,
            client=self.client_identity,
            scope=(self.source,),
        )

    def intent_read_eligible(self, intent):
        """Evaluate read-eligible private planning only, not mutation permission."""
        return (
            intent.actor_key == self.actor_key
            and intent.client == self.client_identity
            and intent.scope == (self.source,)
            and self.read_eligible()
        )

    def private_attempt(self, *, reviewable_fixture=False):
        """Copy/retention inputs are explicit fixture claims, not a real inventory."""
        from integration_state import shared_requests as durable
        from integration_state.s7_deletion_requests import CopyImpact

        intent = self.intent()
        request = durable.record_private_intent(
            self.namespace.pk, intent, authorize=self.intent_read_eligible
        )
        impact = CopyImpact(
            copy_key='private-fixture-copy-claim',
            source=self.source,
            copy_revision='fixture-only-revision' if reviewable_fixture else None,
            owners=(self.client_identity,) if reviewable_fixture else None,
            attribution='source_claimed' if reviewable_fixture else 'unknown',
            retention='reviewable' if reviewable_fixture else 'indefinite',
        )
        stored = durable.record_copy_impact(
            request.request_pk,
            impact,
            intent=intent,
            copy_kind='unknown',
            authorize=self.intent_read_eligible,
        )
        pending = durable.start_private_copy_attempt(
            stored.impact_pk,
            intent=intent,
            impact=impact,
            attempt=uuid4(),
            max_attempts=2,
            authorize=self.intent_read_eligible,
        )
        return intent, impact, request, stored, pending

    def test_actual_private_carriers_retain_unknowns_default_retention_and_replay(self):
        """Native view eligibility does not turn private rows into deletion authority."""
        from integration_state import shared_requests as durable
        from integration_state.models import (
            PrivateCopyAttempt,
            PrivateCopyImpact,
            PrivateDeletionRequest,
        )
        from integration_state.s7_deletion_requests import CopyProgress, DeletionIntent

        intent, impact, request, stored, pending = self.private_attempt()
        self.assertIs(type(request.intent), DeletionIntent)
        self.assertIs(type(pending.progress), CopyProgress)
        self.assertEqual(request.intent, intent)
        self.assertEqual(request.intent.default_retention, 'indefinite')
        self.assertEqual(stored.impact.retention, 'indefinite')
        self.assertIsNone(stored.impact.copy_revision)
        self.assertIsNone(stored.impact.owners)
        self.assertEqual(stored.impact.attribution, 'unknown')
        self.assertEqual(pending.progress.status, 'uncertain')
        self.assertEqual(
            durable.record_private_intent(
                self.namespace.pk, intent, authorize=self.intent_read_eligible
            ),
            request,
        )
        self.assertEqual(
            durable.record_copy_impact(
                request.request_pk,
                impact,
                intent=intent,
                copy_kind='unknown',
                authorize=self.intent_read_eligible,
            ),
            stored,
        )
        self.assertEqual(
            durable.read_private_copy_progress(
                stored.impact_pk,
                intent=intent,
                impact=impact,
                authorize=self.intent_read_eligible,
            ),
            pending,
        )
        self.assertEqual(PrivateDeletionRequest.objects.count(), 1)
        self.assertEqual(PrivateCopyImpact.objects.count(), 1)
        self.assertEqual(PrivateCopyAttempt.objects.count(), 1)
        for result in (request, stored, pending):
            for name in (
                'native_authorized',
                'coverage_complete',
                'deletion_applied',
                'erasure_complete',
            ):
                assert_literal_bool(self, getattr(result, name), False)
        self.assertTrue(pending.progress.synthetic_only)
        self.assert_native_untouched()

    def test_same_key_private_replay_withholds_when_independent_grant_is_revoked(self):
        """A retained request key is not a durable current native-read grant."""
        from integration_state import shared_requests as durable
        from integration_state.models import PrivateDeletionRequest

        intent = self.intent()
        request = durable.record_private_intent(
            self.namespace.pk, intent, authorize=self.intent_read_eligible
        )
        ClientScopeGrant.objects.filter(user=self.reader, client=self.owner).delete()
        try:
            self.assertTrue(
                scoped_machines(self.reader).filter(pk=self.machine.pk).exists()
            )
            self.assertFalse(explicitly_granted_client_ids(self.reader).exists())
            with self.assertRaises(PermissionError):
                durable.record_private_intent(
                    self.namespace.pk, intent, authorize=self.intent_read_eligible
                )
            with self.assertRaises(PermissionError):
                durable.read_private_intent(
                    request.request_pk,
                    intent=intent,
                    authorize=self.intent_read_eligible,
                )
            self.assertEqual(PrivateDeletionRequest.objects.count(), 1)
        finally:
            ClientScopeGrant.objects.create(user=self.reader, client=self.owner)
        self.assertEqual(
            durable.read_private_intent(
                request.request_pk, intent=intent, authorize=self.intent_read_eligible
            ),
            request,
        )
        self.assert_native_untouched()

    def test_role_revocation_after_committed_intent_withholds_not_erases(self):
        """Read eligibility can fail after the durable private intent already exists."""
        from integration_state import shared_requests as durable
        from integration_state.models import PrivateDeletionRequest

        intent = self.intent()
        role = RuleSet.objects.filter(group=self.group, name='work_order')
        calls = []

        def revoke_then_read(value):
            calls.append(True)
            if len(calls) == 2:
                role.update(can_view=False)
            return self.intent_read_eligible(value)

        try:
            with self.assertRaises(PermissionError):
                durable.record_private_intent(
                    self.namespace.pk, intent, authorize=revoke_then_read
                )
            self.assertEqual(len(calls), 2)
            self.assertEqual(PrivateDeletionRequest.objects.count(), 1)
            self.assertTrue(
                explicitly_granted_client_ids(self.reader)
                .filter(client_id=self.owner.pk)
                .exists()
            )
        finally:
            role.update(can_view=True)
        request_pk = PrivateDeletionRequest.objects.get().pk
        restored = durable.read_private_intent(
            request_pk, intent=intent, authorize=self.intent_read_eligible
        )
        self.assertEqual(restored.intent, intent)
        self.assertFalse(restored.native_authorized)
        self.assert_native_untouched()

    def test_copy_claim_change_cannot_adopt_retained_key_at_unknown_revision(self):
        """Unknown unchanged revision is not unchanged owner/retention evidence."""
        from integration_state import shared_requests as durable
        from integration_state.models import PrivateCopyImpact

        intent, impact, request, stored, pending = self.private_attempt()
        for changed in (
            replace(
                impact, owners=(self.client_identity,), attribution='source_claimed'
            ),
            replace(impact, retention='protected'),
        ):
            with (
                self.subTest(retention=changed.retention),
                self.assertRaises(SharedConflictError),
            ):
                durable.record_copy_impact(
                    request.request_pk,
                    changed,
                    intent=intent,
                    copy_kind='unknown',
                    authorize=self.intent_read_eligible,
                )
        with self.assertRaises(PermissionError):
            durable.read_private_intent(
                request.request_pk,
                intent=replace(intent, actor_key='another-actor'),
                authorize=self.intent_read_eligible,
            )
        self.assertEqual(PrivateCopyImpact.objects.count(), 1)
        self.assertEqual(
            durable.read_private_copy_progress(
                stored.impact_pk,
                intent=intent,
                impact=impact,
                authorize=self.intent_read_eligible,
            ),
            pending,
        )
        self.assert_native_untouched()

    def test_synthetic_progress_replay_nonregression_and_cumulative_budget_persist(
        self,
    ):
        """A reviewable fixture label never becomes native approval or real erasure."""
        from integration_state import shared_requests as durable
        from integration_state.models import PrivateCopyAttempt

        intent, impact, _, stored, initial = self.private_attempt(
            reviewable_fixture=True
        )
        pending = durable.record_private_copy_progress(
            initial,
            replace(initial.progress, sequence=1, status='pending'),
            intent=intent,
            impact=impact,
            authorize=self.intent_read_eligible,
        )
        uncertain = durable.record_private_copy_progress(
            pending,
            replace(pending.progress, sequence=2, status='uncertain'),
            intent=intent,
            impact=impact,
            authorize=self.intent_read_eligible,
        )
        self.assertEqual(
            durable.record_private_copy_progress(
                uncertain,
                pending.progress,
                intent=intent,
                impact=impact,
                authorize=self.intent_read_eligible,
            ),
            uncertain,
        )
        retried = durable.retry_private_copy_attempt(
            uncertain,
            intent=intent,
            impact=impact,
            attempt=uuid4(),
            authorize=self.intent_read_eligible,
        )
        self.assertEqual(retried.progress.attempts_used, 2)
        self.assertEqual(retried.progress.max_attempts, 2)
        self.assertEqual(
            retried.progress.attempt_history[0], initial.progress.attempt_id
        )
        pending_again = durable.record_private_copy_progress(
            retried,
            replace(retried.progress, sequence=1, status='pending'),
            intent=intent,
            impact=impact,
            authorize=self.intent_read_eligible,
        )
        terminal = durable.record_private_copy_progress(
            pending_again,
            replace(pending_again.progress, sequence=2, status='uncertain'),
            intent=intent,
            impact=impact,
            authorize=self.intent_read_eligible,
        )
        with self.assertRaises(ValueError):
            durable.retry_private_copy_attempt(
                terminal,
                intent=intent,
                impact=impact,
                attempt=uuid4(),
                authorize=self.intent_read_eligible,
            )
        self.assertEqual(PrivateCopyAttempt.objects.count(), 2)
        self.assertEqual(
            durable.read_private_copy_progress(
                stored.impact_pk,
                intent=intent,
                impact=impact,
                authorize=self.intent_read_eligible,
            ),
            terminal,
        )
        self.assertTrue(terminal.progress.synthetic_only)
        self.assertFalse(terminal.deletion_applied)
        self.assertFalse(terminal.erasure_complete)
        self.assert_native_untouched()


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='s5-owner',
)
class NativeCoverageRegisterTests(NativePlanningContext, TransactionTestCase):
    """Read actual S5 progress into private limitations with current native access."""

    def completed_scan(self):
        """Persist a nonempty real native subset using only S5-owned services."""
        from integration_state.shared_reconciliation import (
            accept_scan_page,
            begin_scan,
            read_scan,
            reserve_scan_page,
        )

        initial = begin_scan(
            self.namespace.pk,
            'private-coverage-scan',
            self.binding,
            max_pages=1,
            max_records=1,
            authorize=self.scan_read_eligible,
        )
        reserved = reserve_scan_page(initial, authorize=self.scan_read_eligible)
        completed = accept_scan_page(
            reserved,
            self.native_page(),
            attempt=reserved.progress.pending_attempt,
            authorize=self.scan_read_eligible,
        )
        self.assertEqual(completed.progress.seen, frozenset((self.source,)))
        self.assertEqual(completed.progress.state, 'transport_complete')
        self.assertFalse(connection.in_atomic_block)
        return read_scan(
            completed.scan_id, binding=self.binding, authorize=self.scan_read_eligible
        )

    @staticmethod
    def gap():
        """Record a labeled unqualified boundary, not real all-writer evidence."""
        from integration_state.s7_reconciliation import CoverageGap

        return CoverageGap(
            target_kind='writer',
            target_key='synthetic-unqualified-nonplacement-writer',
            capability='reconciliation',
            reason='unknown',
            evidence_key='synthetic-boundary-not-coverage-proof',
        )

    def test_actual_durable_progress_keeps_limitations_and_current_grant_withholding(
        self,
    ):
        """A persisted nonempty scan is useful without qualifying native population."""
        from integration_state.models import (
            ReconciliationAttempt,
            ReconciliationIdentity,
            ReconciliationScan,
        )
        from integration_state.s7_reconciliation import coverage_register
        from integration_state.shared_reconciliation import read_scan

        completed = self.completed_scan()
        gaps = (self.gap(),)
        report = coverage_register(
            completed.progress, gaps, authorize=self.scan_read_eligible
        )
        self.assertEqual(report.binding, self.binding)
        self.assertEqual(report.transport_state, 'transport_complete')
        self.assertEqual(report.gaps, gaps)
        self.assertEqual(report.status, 'unqualified')
        self.assertFalse(report.coverage_complete)
        self.assertFalse(report.population_complete)
        self.assertFalse(report.native_authorized)
        self.assertFalse(report.removal_complete)
        self.assertFalse(report.deletion_applied)
        self.assertEqual(report.tombstones, ())
        ClientScopeGrant.objects.filter(user=self.reader, client=self.owner).delete()
        try:
            self.assertTrue(
                scoped_machines(self.reader).filter(pk=self.machine.pk).exists()
            )
            self.assertFalse(explicitly_granted_client_ids(self.reader).exists())
            with self.assertRaises(PermissionError):
                coverage_register(
                    completed.progress, gaps, authorize=self.scan_read_eligible
                )
            self.assertEqual(
                ReconciliationScan.objects.get().version, completed.version
            )
        finally:
            ClientScopeGrant.objects.create(user=self.reader, client=self.owner)
        fresh = read_scan(
            completed.scan_id, binding=self.binding, authorize=self.scan_read_eligible
        )
        self.assertEqual(fresh, completed)
        self.assertEqual(
            coverage_register(fresh.progress, gaps, authorize=self.scan_read_eligible),
            report,
        )
        self.assertEqual(ReconciliationAttempt.objects.count(), 1)
        self.assertEqual(ReconciliationIdentity.objects.count(), 1)
        self.assert_native_untouched()

    def test_grant_revoked_during_construction_withholds_without_rewriting_scan(self):
        """Final native grant loss cannot disclose the already constructed register."""
        from unittest.mock import patch

        from integration_state.models import ReconciliationScan
        from integration_state.s7_reconciliation import (
            ScanCoverageRegister,
            coverage_register,
        )
        from integration_state.shared_reconciliation import read_scan

        completed = self.completed_scan()
        self.assertEqual(
            coverage_register(
                completed.progress, (self.gap(),), authorize=self.scan_read_eligible
            ).status,
            'unqualified',
        )

        def construct(**values):
            result = ScanCoverageRegister(**values)
            ClientScopeGrant.objects.filter(
                user=self.reader, client=self.owner
            ).delete()
            return result

        try:
            with patch(
                'integration_state.s7_reconciliation.ScanCoverageRegister',
                side_effect=construct,
            ) as constructor:
                with self.assertRaises(PermissionError):
                    coverage_register(
                        completed.progress,
                        (self.gap(),),
                        authorize=self.scan_read_eligible,
                    )
                constructor.assert_called_once()
            self.assertTrue(
                scoped_machines(self.reader).filter(pk=self.machine.pk).exists()
            )
            self.assertEqual(
                ReconciliationScan.objects.get().version, completed.version
            )
        finally:
            ClientScopeGrant.objects.create(user=self.reader, client=self.owner)
        self.assertEqual(
            read_scan(
                completed.scan_id,
                binding=self.binding,
                authorize=self.scan_read_eligible,
            ),
            completed,
        )
        self.assert_native_untouched()


class NativeS6CoverageCompositionTests(S6AIRequestFixture, TransactionTestCase):
    """Compose public S6/S5/S7 APIs inside actual local ASGI DB sessions."""

    def setUp(self):
        """Reuse only the plain source-owner fixture in the owned database."""
        super().setUp()
        self.namespace = SourceNamespace.objects.create(
            environment='s6-test', active=True
        )

    def tearDown(self):
        """Deactivate only this fixture namespace before destructive test flush."""
        try:
            SourceNamespace.objects.filter(pk=self.namespace.pk).update(active=False)
        finally:
            super().tearDown()

    def prepare_scan(self, **changes):
        """Obtain current source authority, never a supplied test principal."""
        from machine_health.services.integration_s6_scan import prepare_history_scan

        return prepare_history_scan(
            environment='s6-test',
            machine_pk=self.machine.pk,
            source_pk=self.source.pk,
            binding_pk=self.binding.pk,
            window=self.window,
            adapter=changes.pop('adapter', self.adapter),
            page_size=1,
            **changes,
        )

    def complete_scan(self, prepared, *, request_key='s7-source-composition'):
        """Accept source-derived pages through actual S5 committed reservations."""
        snapshot = prepared.begin(
            namespace_id=self.namespace.pk,
            epoch=self.namespace.epoch,
            request_key=request_key,
            max_pages=4,
            max_records=8,
        )
        while snapshot.progress.state == 'running':
            reserved = prepared.reserve(snapshot)
            snapshot = prepared.accept(
                reserved, attempt=reserved.progress.pending_attempt
            )
        return snapshot

    def limitations(self, prepared, snapshot, *, gaps=None):
        """Recheck source/current stored binding through the public read operation."""
        from integration_state.s7_reconciliation import CoverageGap, coverage_register
        from machine_health.services.integration_s6_scan import (
            HistoryScanUnavailableError,
        )

        if gaps is None:
            gaps = (
                CoverageGap(
                    target_kind='source',
                    target_key='synthetic-recorded-window',
                    capability='population',
                    reason='unknown',
                    evidence_key='fixture-window-not-physical-coverage-proof',
                ),
            )

        def eligible(binding):
            try:
                current = prepared.read(
                    snapshot.scan_id,
                    namespace_id=self.namespace.pk,
                    epoch=self.namespace.epoch,
                )
            except HistoryScanUnavailableError:
                return False
            return binding == snapshot.progress.binding and current == snapshot

        return coverage_register(snapshot.progress, gaps, authorize=eligible)

    def assert_unqualified(self, report, snapshot):
        """A real selected transport or empty gap list grants no source capability."""
        self.assertEqual(report.binding, snapshot.progress.binding)
        self.assertEqual(report.transport_state, snapshot.progress.state)
        self.assertEqual(report.status, 'unqualified')
        for name in (
            'coverage_complete',
            'population_complete',
            'native_authorized',
            'removal_complete',
            'deletion_applied',
        ):
            assert_literal_bool(self, getattr(report, name), False)
        self.assertEqual(report.tombstones, ())

    def native_source_rows(self):
        """Observe independent actual synthetic source rows, not progress projections."""
        return tuple(
            type(value).objects.filter(pk=value.pk).values().get()
            for value in (self.machine, self.source, self.point, self.binding)
        )

    def set_source_records(self, identities):
        """Create independent literal fixture truth with the real source producer."""
        from machine_health.services.integration_s6_adapter import FixtureHistoryAdapter
        from machine_health.tests.test_integration_s6_wire import observation

        self.adapter = FixtureHistoryAdapter.from_records(
            source=self.identity,
            mapping=self.mapping,
            coverage=self.window,
            generation='fixture-generation',
            records=tuple(
                observation(
                    value={'private-s7-scan-body': index},
                    source=self.identity,
                    external_key=self.point.path,
                    unit=self.point.unit,
                    source_observation_id=identity,
                )
                for index, identity in enumerate(identities)
            ),
        )

    def test_nonempty_source_pages_feed_real_progress_and_private_register(self):
        """Literal IDs and committed replay survive the actual source-shaped chain."""
        from integration_state.models import (
            ReconciliationAttempt,
            ReconciliationIdentity,
            ReconciliationScan,
        )

        self.set_source_records(('7', ' 7 '))
        native_before = self.native_source_rows()

        def operation():
            prepared = self.prepare_scan()
            snapshot = self.complete_scan(prepared)
            self.assertEqual(snapshot.progress.offset, 2)
            self.assertEqual(snapshot.progress.expected_total, 2)
            self.assertEqual(
                {item.source_id for item in snapshot.progress.seen}, {'7', ' 7 '}
            )
            self.assertTrue(
                all(type(item.source_id) is str for item in snapshot.progress.seen)
            )
            report = self.limitations(prepared, snapshot)
            self.assert_unqualified(report, snapshot)
            self.assertEqual(report.transport_state, 'transport_complete')
            self.assertEqual(self.limitations(prepared, snapshot), report)
            replay = prepared.accept(snapshot, attempt=snapshot.progress.last_attempt)
            self.assertEqual(replay, snapshot)
            self.assertEqual(self.limitations(prepared, replay), report)
            self.assert_unqualified(
                self.limitations(prepared, snapshot, gaps=()), snapshot
            )
            self.assertEqual(ReconciliationScan.objects.get().version, snapshot.version)
            self.assertEqual(ReconciliationAttempt.objects.count(), 2)
            self.assertEqual(ReconciliationIdentity.objects.count(), 2)
            self.assertNotIn(
                'private-s7-scan-body', str(ReconciliationScan.objects.get().checkpoint)
            )
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)
        self.assertEqual(self.native_source_rows(), native_before)

    def test_source_role_scope_and_grant_withhold_register_independently(self):
        """Withhold one real prerequisite while the others stay demonstrably positive."""
        from tasks.scope import MaintenanceScope

        from integration_state.models import ReconciliationScan

        allowed = {MaintenanceScope(None, None, self.tenant.pk)}

        def operation():
            prepared = self.prepare_scan()
            snapshot = self.complete_scan(prepared)
            original = self.limitations(prepared, snapshot)
            self.assertFalse(self.actor.is_superuser)
            for predicate in ('role', 'scope', 'grant'):
                with self.subTest(predicate=predicate):
                    selected = set() if predicate == 'scope' else allowed
                    with self.settings(
                        AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda _actor, selected=selected: (
                            selected
                        )
                    ):
                        try:
                            if predicate == 'role':
                                RuleSet.objects.filter(pk=self.rule.pk).update(
                                    can_view=False
                                )
                            if predicate == 'grant':
                                ClientScopeGrant.objects.filter(
                                    pk=self.grant.pk
                                ).delete()
                            self.assertEqual(
                                scoped_machines(self.actor)
                                .filter(pk=self.machine.pk)
                                .exists(),
                                predicate != 'scope',
                            )
                            self.assertEqual(
                                ClientScopeGrant.objects.filter(
                                    pk=self.grant.pk
                                ).exists(),
                                predicate != 'grant',
                            )
                            self.assertEqual(
                                RuleSet.objects.get(pk=self.rule.pk).can_view,
                                predicate != 'role',
                            )
                            with self.assertRaises(PermissionError):
                                self.limitations(prepared, snapshot)
                            self.assertEqual(
                                ReconciliationScan.objects.get().version,
                                snapshot.version,
                            )
                        finally:
                            RuleSet.objects.filter(pk=self.rule.pk).update(
                                can_view=True
                            )
                            ClientScopeGrant.objects.get_or_create(
                                pk=self.grant.pk, user=self.actor, client=self.tenant
                            )
                    self.assertEqual(self.limitations(prepared, snapshot), original)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_source_grant_revoked_in_final_register_constructor_withholds(self):
        """Refresh real S6 source authority after final register construction."""
        from unittest.mock import patch

        from tasks.scope import MaintenanceScope

        from integration_state.models import ReconciliationScan
        from integration_state.s7_reconciliation import ScanCoverageRegister

        allowed = {MaintenanceScope(None, None, self.tenant.pk)}

        def operation():
            prepared = self.prepare_scan()
            snapshot = self.complete_scan(prepared)
            positive = self.limitations(prepared, snapshot)

            def revoke(**values):
                result = ScanCoverageRegister(**values)
                ClientScopeGrant.objects.filter(pk=self.grant.pk).delete()
                return result

            with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda _actor: allowed):
                try:
                    with patch(
                        'integration_state.s7_reconciliation.ScanCoverageRegister',
                        side_effect=revoke,
                    ) as constructor:
                        with self.assertRaises(PermissionError):
                            self.limitations(prepared, snapshot)
                        constructor.assert_called_once()
                    self.assertTrue(
                        scoped_machines(self.actor).filter(pk=self.machine.pk).exists()
                    )
                    self.assertEqual(
                        ReconciliationScan.objects.get().version, snapshot.version
                    )
                finally:
                    ClientScopeGrant.objects.get_or_create(
                        pk=self.grant.pk, user=self.actor, client=self.tenant
                    )
            self.assertEqual(self.limitations(prepared, snapshot), positive)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_empty_incomplete_and_unknown_sources_are_not_complete_coverage(self):
        """Actual tied-gap, complete-empty and unsupported source outcomes stay distinct."""
        from integration_state.models import ReconciliationScan
        from machine_health.services.integration_s6_scan import (
            HistoryScanUnavailableError,
        )

        def operation():
            for kind in ('empty', 'incomplete'):
                with self.subTest(kind=kind):
                    ids = () if kind == 'empty' else ('tie:1', 'tie:2', 'tie:3')
                    self.set_source_records(ids)
                    arguments = {} if kind == 'empty' else {'max_samples_per_read': 2}
                    prepared = self.prepare_scan(**arguments)
                    snapshot = self.complete_scan(prepared, request_key=kind)
                    report = self.limitations(prepared, snapshot)
                    self.assert_unqualified(report, snapshot)
                    self.assertEqual(snapshot.progress.offset, 0)
                    self.assertEqual(snapshot.progress.seen, frozenset())
                    if kind == 'empty':
                        self.assertEqual(report.transport_state, 'transport_complete')
                        self.assertEqual(snapshot.progress.expected_total, 0)
                    else:
                        self.assertEqual(report.transport_state, 'incomplete')
                        self.assertIsNone(snapshot.progress.expected_total)
            count = ReconciliationScan.objects.count()
            with self.assertRaises(HistoryScanUnavailableError):
                self.prepare_scan(adapter=None)
            self.assertEqual(ReconciliationScan.objects.count(), count)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_expired_invocation_withholds_until_fresh_source_authorized_read(self):
        """A persisted checkpoint cannot prolong the original source capability."""
        from integration_state.models import ReconciliationScan

        def initial():
            prepared = self.prepare_scan()
            snapshot = self.complete_scan(prepared)
            report = self.limitations(prepared, snapshot)
            self.assert_unqualified(report, snapshot)
            return prepared, snapshot, report

        old, snapshot, report = self.run_boundary(initial, method='session')
        with self.assertRaises(PermissionError):
            self.limitations(old, snapshot)
        self.assertEqual(ReconciliationScan.objects.get().version, snapshot.version)

        def resumed():
            prepared = self.prepare_scan()
            actual = prepared.read(
                snapshot.scan_id,
                namespace_id=self.namespace.pk,
                epoch=self.namespace.epoch,
            )
            self.assertEqual(actual, snapshot)
            self.assertEqual(self.limitations(prepared, actual), report)
            return True

        assert_literal_bool(self, self.run_boundary(resumed, method='session'), True)
