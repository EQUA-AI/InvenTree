"""Trusted synthetic serving observations over actual durable native placement."""

from django.db import connection
from django.test import TransactionTestCase, override_settings

from assets.tests.integration_s5_test_support import NativePlacementFixture


class SyntheticServingAdapter:
    """Serve independently staged values, never an echoed expected placement."""

    def __init__(self, binding, placement):
        """Keep the independent serving store distinct from request expectations."""
        self.binding = binding
        self.placement = placement
        self.generation = 'synthetic-generation-1'
        self.observations = 0
        self.verifications = 0

    def _record(self):
        """Read this double's actually served generation and source coordinates."""
        from assets.integration_s5_contracts import PlacementServingObservation

        return PlacementServingObservation(
            binding=self.binding, placement=self.placement, generation=self.generation
        )

    def observe_placement(self, binding, machine):
        """The real acknowledgement path must leave its DB transaction for IO."""
        if connection.in_atomic_block:
            raise AssertionError('Serving IO occurred inside the ownership transaction')
        self.observations += 1
        return self._record()

    def verify_observation(self, observation):
        """Independently compare with the current staged serving store."""
        if connection.in_atomic_block:
            raise AssertionError('Serving verification held native source locks')
        self.verifications += 1
        return observation == self._record()


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class PlacementAcknowledgementTests(NativePlacementFixture, TransactionTestCase):
    """No public actor claim or operational source effect is an acknowledgement."""

    def setUp(self):
        """Stage a projection from independent synthetic serving facts."""
        super().setUp()
        from assets.integration_contracts import (
            ClientSourceIdentity,
            MachineSourceIdentity,
        )
        from assets.integration_s5_contracts import (
            PlacementExpectation,
            ProjectionBinding,
        )
        from integration_state.services import (
            activate_namespace,
            bootstrap_projection,
            prepare_projection,
        )

        self.namespace = activate_namespace('synthetic-s5')
        self.enrollment = prepare_projection(
            self.namespace.pk, 'synthetic-projection', self.owner.pk
        )
        bootstrap_projection(self.enrollment.pk)
        self.binding = ProjectionBinding(
            environment='synthetic-s5',
            namespace_id=self.namespace.pk,
            namespace_epoch=self.namespace.epoch,
            enrollment_id=self.enrollment.pk,
            configuration_epoch=self.enrollment.epoch,
            projection='synthetic-projection',
            client=ClientSourceIdentity('synthetic-s5', self.owner.pk),
        )
        self.served = SyntheticServingAdapter(
            self.binding,
            PlacementExpectation(
                machine=MachineSourceIdentity('synthetic-s5', self.machine.pk),
                client=ClientSourceIdentity('synthetic-s5', self.owner.pk),
                location=None,
                native_version=0,
                fence=1,
                namespace_epoch=self.namespace.epoch,
            ),
        )

    def test_latest_serving_ack_and_duplicate_are_durable_without_source_writes(self):
        """Only independently verified latest placement is accepted, idempotently."""
        from assets.integration_s5_ack import acknowledge_latest
        from assets.models import (
            AssetMachine,
            MachineLocationTransfer,
            MachinePlacementHistory,
        )
        from integration_state.models import PlacementAcknowledgement, SourceChange

        before = list(AssetMachine.objects.values())
        self.assertTrue(
            acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        )
        accepted = PlacementAcknowledgement.objects.get()
        self.assertEqual(accepted.source_revision, 1)
        self.assertEqual(accepted.native_version, 0)
        self.assertIsNone(accepted.location_id)
        self.assertEqual(accepted.serving_generation, self.served.generation)
        self.assertTrue(
            acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        )
        self.assertEqual(PlacementAcknowledgement.objects.get().pk, accepted.pk)
        self.assertEqual(self.served.observations, 2)
        self.assertEqual(self.served.verifications, 2)
        self.assertEqual(list(AssetMachine.objects.values()), before)
        self.assertEqual(SourceChange.objects.count(), 1)
        self.assertFalse(MachineLocationTransfer.objects.exists())
        self.assertFalse(MachinePlacementHistory.objects.exists())
        self.enrollment.refresh_from_db()
        self.assertTrue(self.enrollment.generation_known)
        self.assertEqual(self.enrollment.served_generation, self.served.generation)

    def test_verifier_cannot_substitute_an_unproved_generation_after_validation(self):
        """Verification cannot mutate the proof object and change persisted meaning."""
        from assets.integration_s5_ack import PlacementProofError, acknowledge_latest
        from integration_state.models import PlacementAcknowledgement

        verify = self.served.verify_observation

        def mutate(observation):
            verified = verify(observation)
            object.__setattr__(observation, 'generation', 'unproved-generation')
            return verified

        self.served.verify_observation = mutate
        with self.assertRaises(PlacementProofError):
            acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        self.assertFalse(PlacementAcknowledgement.objects.exists())

    def test_forged_boolean_native_version_is_not_exact_integer_proof(self):
        """Frozen carriers are revalidated, not trusted through Python equality."""
        from assets.integration_s5_ack import PlacementProofError, acknowledge_latest
        from integration_state.models import PlacementAcknowledgement

        object.__setattr__(self.served.placement, 'native_version', False)
        with self.assertRaises(PlacementProofError):
            acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        self.assertFalse(PlacementAcknowledgement.objects.exists())

    def test_spoofed_binding_environment_cannot_compare_equal_to_current_source(self):
        """Validate each binding label's exact type before comparing values."""
        from assets.integration_s5_ack import PlacementProofError, acknowledge_latest
        from integration_state.models import PlacementAcknowledgement

        class SpoofedEnvironment(str):
            """Deliberately dishonest comparator, not a real environment label."""

            __hash__ = str.__hash__

            def __eq__(self, other):
                """Make a different source appear equal without exact typing."""
                return True

        object.__setattr__(
            self.served.binding, 'environment', SpoofedEnvironment('different-source')
        )
        with self.assertRaises(PlacementProofError):
            acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        self.assertFalse(PlacementAcknowledgement.objects.exists())

    def test_source_write_during_serving_io_prevents_stale_compare_and_set(self):
        """A real native commit after reservation cannot be cleared by old proof."""
        from assets.integration_s5_ack import PlacementProofError, acknowledge_latest
        from integration_state.models import PlacementAcknowledgement, SourceState

        observe = self.served.observe_placement

        def advance(binding, machine):
            observation = observe(binding, machine)
            self.move()
            return observation

        self.served.observe_placement = advance
        with self.assertRaises(PlacementProofError):
            acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.placement_version, 1)
        self.assertEqual(SourceState.objects.get().revision, 2)
        self.assertFalse(PlacementAcknowledgement.objects.exists())

    def test_older_observer_cannot_overwrite_a_newer_committed_observation(self):
        """The durable observation fence rejects delayed work, not just versions."""
        from assets.integration_s5_ack import PlacementProofError, acknowledge_latest
        from integration_state.models import PlacementAcknowledgement

        newer = SyntheticServingAdapter(self.binding, self.served.placement)
        newer.generation = 'synthetic-newer-generation'
        observe = self.served.observe_placement

        def reorder(binding, machine):
            observation = observe(binding, machine)
            acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=newer)
            return observation

        self.served.observe_placement = reorder
        with self.assertRaises(PlacementProofError):
            acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        self.assertEqual(
            PlacementAcknowledgement.objects.get().serving_generation, newer.generation
        )
        self.enrollment.refresh_from_db()
        self.assertTrue(self.enrollment.generation_known)
        self.assertEqual(self.enrollment.served_generation, newer.generation)

    def test_native_counter_unchanged_aba_still_requires_the_latest_aspect_fence(self):
        """An earlier unassigned tuple is not proof after a direct placement ABA."""
        from dataclasses import replace

        from assets.integration_s5_ack import PlacementProofError, acknowledge_latest
        from assets.models import AssetMachine
        from integration_state.models import PlacementAcknowledgement

        AssetMachine.objects.filter(pk=self.machine.pk).update(
            physical_location=self.destination
        )
        AssetMachine.objects.filter(pk=self.machine.pk).update(physical_location=None)
        with self.assertRaises(PlacementProofError):
            acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        self.assertFalse(PlacementAcknowledgement.objects.exists())
        self.served.placement = replace(self.served.placement, fence=3)
        self.served.generation = 'synthetic-aba-generation'
        self.assertTrue(
            acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        )
        accepted = PlacementAcknowledgement.objects.get()
        self.assertEqual((accepted.native_version, accepted.source_revision), (0, 3))

    def test_serving_regression_withdraws_current_generation_without_erasing_receipt(
        self,
    ):
        """A later old-generation read does not inherit an earlier latest ack."""
        from dataclasses import replace

        from assets.integration_contracts import LocationSourceIdentity
        from assets.integration_s5_ack import PlacementProofError, acknowledge_latest
        from integration_state.models import PlacementAcknowledgement

        old = self.served.placement
        self.move()
        self.served.placement = replace(
            old,
            location=LocationSourceIdentity('synthetic-s5', self.destination.pk),
            native_version=1,
            fence=2,
        )
        self.served.generation = 'synthetic-generation-2'
        acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        self.served.placement = old
        self.served.generation = 'synthetic-generation-1'
        with self.assertRaises(PlacementProofError):
            acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        accepted = PlacementAcknowledgement.objects.get()
        self.assertEqual((accepted.native_version, accepted.source_revision), (1, 2))
        self.assertEqual(accepted.serving_generation, 'synthetic-generation-2')
        self.enrollment.refresh_from_db()
        self.assertFalse(self.enrollment.generation_known)

    def test_wrong_configuration_and_source_coordinates_are_rejected(self):
        """Independently valid foreign observations are not the selected population."""
        from dataclasses import replace
        from uuid import uuid4

        from assets.integration_contracts import (
            ClientSourceIdentity,
            LocationSourceIdentity,
            MachineSourceIdentity,
        )
        from assets.integration_s5_ack import PlacementProofError, acknowledge_latest
        from integration_state.models import PlacementAcknowledgement

        binding, placement = self.served.binding, self.served.placement
        different_epoch = uuid4()
        other_client = ClientSourceIdentity('synthetic-s5', self.owner.pk + 1)
        variants = [
            (replace(binding, namespace_id=binding.namespace_id + 1), placement),
            (replace(binding, enrollment_id=binding.enrollment_id + 1), placement),
            (replace(binding, configuration_epoch=uuid4()), placement),
            (replace(binding, projection='different-projection'), placement),
            (
                replace(binding, namespace_epoch=different_epoch),
                replace(placement, namespace_epoch=different_epoch),
            ),
            (
                replace(binding, client=other_client),
                replace(placement, client=other_client),
            ),
            (
                binding,
                replace(
                    placement,
                    machine=MachineSourceIdentity('synthetic-s5', self.machine.pk + 1),
                ),
            ),
            (
                binding,
                replace(
                    placement,
                    location=LocationSourceIdentity(
                        'synthetic-s5', self.destination.pk
                    ),
                ),
            ),
            (binding, replace(placement, native_version=1)),
            (binding, replace(placement, fence=2)),
            (
                replace(
                    binding,
                    environment='foreign-source',
                    client=ClientSourceIdentity('foreign-source', self.owner.pk),
                ),
                replace(
                    placement,
                    machine=MachineSourceIdentity('foreign-source', self.machine.pk),
                    client=ClientSourceIdentity('foreign-source', self.owner.pk),
                ),
            ),
        ]
        for index, (foreign_binding, foreign_placement) in enumerate(variants):
            with self.subTest(index=index):
                self.served.binding, self.served.placement = (
                    foreign_binding,
                    foreign_placement,
                )
                try:
                    with self.assertRaises(PlacementProofError):
                        acknowledge_latest(
                            self.enrollment.pk, self.machine.pk, adapter=self.served
                        )
                    self.assertFalse(PlacementAcknowledgement.objects.exists())
                finally:
                    self.served.binding, self.served.placement = binding, placement

    def test_invented_generation_and_truthy_verdict_are_not_serving_proof(self):
        """An echo and a truthy object cannot substitute for exact verification."""
        from dataclasses import replace

        from assets.integration_s5_ack import PlacementProofError, acknowledge_latest
        from integration_state.models import PlacementAcknowledgement

        observe, verify = self.served.observe_placement, self.served.verify_observation
        self.served.observe_placement = lambda binding, machine: replace(
            observe(binding, machine), generation='not-the-serving-generation'
        )
        with self.assertRaises(PlacementProofError):
            acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        self.served.observe_placement = observe
        self.served.verify_observation = lambda observation: 1
        try:
            with self.assertRaises(PlacementProofError):
                acknowledge_latest(
                    self.enrollment.pk, self.machine.pk, adapter=self.served
                )
            self.assertFalse(PlacementAcknowledgement.objects.exists())
        finally:
            self.served.verify_observation = verify

    def test_timeout_is_unqualified_and_native_transfer_remains_usable(self):
        """Proof availability never becomes a source write or native prerequisite."""
        from assets.integration_s5_ack import (
            PlacementProofUnavailableError,
            acknowledge_latest,
        )
        from integration_state.models import PlacementAcknowledgement

        acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)

        def timeout(binding, machine):
            raise TimeoutError('Synthetic serving timeout')

        self.served.observe_placement = timeout
        with self.assertRaises(PlacementProofUnavailableError):
            acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        self.enrollment.refresh_from_db()
        self.assertFalse(self.enrollment.generation_known)
        self.assertEqual(PlacementAcknowledgement.objects.get().source_revision, 1)
        self.move()
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.placement_version, 1)

    def test_acknowledgement_store_failure_is_typed_and_never_native_success(self):
        """The atomic final write rolls back without losing native placement."""
        from unittest.mock import patch

        from django.db import DatabaseError

        from assets.integration_s5_ack import acknowledge_latest
        from integration_state.models import PlacementAcknowledgement
        from integration_state.services import PlacementStoreUnavailableError

        with patch.object(
            PlacementAcknowledgement.objects,
            'update_or_create',
            side_effect=DatabaseError('Synthetic metadata failure'),
        ):
            with self.assertRaises(PlacementStoreUnavailableError):
                acknowledge_latest(
                    self.enrollment.pk, self.machine.pk, adapter=self.served
                )
        self.assertFalse(PlacementAcknowledgement.objects.exists())
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.placement_version, 0)

    def test_three_native_versions_unassignment_and_same_key_replay_keep_latest_proof(
        self,
    ):
        """Reordered v1/v2 proofs never clear v3; replay produces no new fence."""
        from dataclasses import replace
        from uuid import uuid4

        from assets.integration_contracts import LocationSourceIdentity
        from assets.integration_s5_ack import PlacementProofError, acknowledge_latest
        from assets.models import MachineLocationTransfer, MachinePlacementHistory
        from integration_state.models import PlacementAcknowledgement, SourceState

        initial = self.served.placement
        self.move()
        first = replace(
            initial,
            location=LocationSourceIdentity('synthetic-s5', self.destination.pk),
            native_version=1,
            fence=2,
        )
        self.served.placement = first
        acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        self.move(destination=None)
        second = replace(initial, native_version=2, fence=3)
        self.served.placement = second
        self.served.generation = 'synthetic-generation-2'
        acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        self.assertIsNone(PlacementAcknowledgement.objects.get().location_id)
        key = uuid4()
        self.move(key=key)
        for stale in (first, second):
            with self.subTest(native_version=stale.native_version):
                self.served.placement = stale
                with self.assertRaises(PlacementProofError):
                    acknowledge_latest(
                        self.enrollment.pk, self.machine.pk, adapter=self.served
                    )
        self.served.placement = replace(first, native_version=3, fence=4)
        self.served.generation = 'synthetic-generation-3'
        acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        self.move(key=key, version=2)
        acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        self.assertEqual(PlacementAcknowledgement.objects.get().source_revision, 4)
        self.assertEqual(SourceState.objects.get().revision, 4)
        self.assertEqual(MachineLocationTransfer.objects.count(), 3)
        self.assertEqual(MachinePlacementHistory.objects.count(), 3)

    def test_replacement_configuration_during_io_cannot_inherit_old_observation(self):
        """Disable and prepare establish a different durable configuration identity."""
        from assets.integration_s5_ack import acknowledge_latest
        from integration_state.models import PlacementAcknowledgement
        from integration_state.services import (
            PlacementStateError,
            bootstrap_projection,
            disable_projection,
            prepare_projection,
        )

        observe = self.served.observe_placement

        def replace_configuration(binding, machine):
            observation = observe(binding, machine)
            disable_projection(self.enrollment.pk)
            replacement = prepare_projection(
                self.namespace.pk, 'synthetic-projection', self.owner.pk
            )
            bootstrap_projection(replacement.pk)
            self.assertNotEqual(replacement.epoch, self.enrollment.epoch)
            return observation

        self.served.observe_placement = replace_configuration
        with self.assertRaises(PlacementStateError):
            acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        self.assertFalse(PlacementAcknowledgement.objects.exists())
        self.enrollment.refresh_from_db()
        self.assertFalse(self.enrollment.active)
        self.assertFalse(self.enrollment.generation_known)

    def test_accepted_acknowledgement_survives_connection_replacement(self):
        """Fresh-session readback observes committed metadata, not Python flags."""
        from assets.integration_s5_ack import acknowledge_latest
        from integration_state.models import (
            PlacementAcknowledgement,
            ProjectionEnrollment,
        )

        acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        accepted = dict(PlacementAcknowledgement.objects.values().get())
        previous = connection.connection
        connection.close()
        self.assertIsNone(connection.connection)
        restored = dict(PlacementAcknowledgement.objects.values().get())
        self.assertIsNot(connection.connection, previous)
        self.assertEqual(restored, accepted)
        self.assertTrue(
            ProjectionEnrollment.objects.get(pk=self.enrollment.pk).generation_known
        )

    def test_outer_transaction_is_refused_before_serving_io_or_reservation(self):
        """A caller cannot accidentally hold native locks during external proof."""
        from django.db import transaction

        from assets.integration_s5_ack import PlacementProofError, acknowledge_latest

        with transaction.atomic():
            with self.assertRaises(PlacementProofError):
                acknowledge_latest(
                    self.enrollment.pk, self.machine.pk, adapter=self.served
                )
        self.assertEqual(self.served.observations, 0)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.observation_revision, 0)

    def test_raw_echo_and_missing_revision_do_not_create_accepted_metadata(self):
        """An exact-looking dictionary or incomplete source record is not proof."""
        from assets.integration_s5_ack import PlacementProofError, acknowledge_latest
        from integration_state.models import PlacementAcknowledgement

        observe = self.served.observe_placement
        self.served.observe_placement = lambda binding, machine: {
            'binding': binding,
            'placement': self.served.placement,
            'generation': self.served.generation,
        }
        with self.assertRaises(PlacementProofError):
            acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        self.served.observe_placement = observe
        object.__setattr__(self.served.placement, 'fence', None)
        with self.assertRaises(PlacementProofError):
            acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        self.assertFalse(PlacementAcknowledgement.objects.exists())

    def test_nested_epoch_mutation_during_verification_cannot_alter_owned_snapshot(
        self,
    ):
        """Even UUID internals passed to a callback are detached from owned proof."""
        from assets.integration_s5_ack import PlacementProofError, acknowledge_latest
        from integration_state.models import PlacementAcknowledgement

        verify = self.served.verify_observation

        def mutate(observation):
            valid = verify(observation)
            object.__setattr__(observation.binding.namespace_epoch, 'int', 0)
            return valid

        self.served.verify_observation = mutate
        with self.assertRaises(PlacementProofError):
            acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        self.assertFalse(PlacementAcknowledgement.objects.exists())
        self.namespace.refresh_from_db()
        self.assertNotEqual(self.namespace.epoch.int, 0)
