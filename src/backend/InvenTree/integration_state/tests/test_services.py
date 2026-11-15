"""Durable placement services over actual native source and real metadata rows."""

from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings

from assets.tests.integration_s5_test_support import NativePlacementFixture
from integration_state.models import SourceNamespace


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class PlacementServiceTests(NativePlacementFixture, TestCase):
    """Internal source state is independent of the actor who changed placement."""

    def test_current_expectation_reads_back_a_committed_native_transfer(self):
        """The service returns source-qualified persisted facts, not a callback flag."""
        from integration_state.services import current_expectation

        namespace = SourceNamespace.objects.create(
            environment='synthetic-s5', active=True
        )
        self.move()
        expected = current_expectation(namespace.pk, self.machine.pk)
        self.assertEqual(expected.machine.environment, 'synthetic-s5')
        self.assertEqual(expected.machine.pk, self.machine.pk)
        self.assertEqual(expected.client.pk, self.owner.pk)
        self.assertEqual(expected.location.pk, self.destination.pk)
        self.assertEqual(expected.native_version, 1)
        self.assertEqual(expected.fence, 1)
        self.assertEqual(expected.namespace_epoch, namespace.epoch)

    def test_corrupted_marker_cannot_replace_authoritative_native_placement(self):
        """A retained tuple is evidence only when it still matches source meaning."""
        from integration_state.models import SourceState
        from integration_state.services import current_expectation

        namespace = SourceNamespace.objects.create(
            environment='synthetic-s5', active=True
        )
        self.move()
        valid = current_expectation(namespace.pk, self.machine.pk)
        state = SourceState.objects.get()
        for field, value in (
            ('location_id', None),
            ('native_version', 0),
            ('deleted', True),
        ):
            original = getattr(state, field)
            with self.subTest(field=field):
                SourceState.objects.filter(pk=state.pk).update(**{field: value})
                try:
                    with self.assertRaisesRegex(ValueError, 'placement'):
                        current_expectation(namespace.pk, self.machine.pk)
                finally:
                    SourceState.objects.filter(pk=state.pk).update(**{field: original})
        self.assertEqual(current_expectation(namespace.pk, self.machine.pk), valid)

    def test_invalid_native_cross_client_location_is_unqualified_not_repaired(self):
        """Capturing a legacy write does not authorize its malformed relationship."""
        from assets.models import AssetMachine, Client
        from integration_state.services import current_expectation

        namespace = SourceNamespace.objects.create(
            environment='synthetic-s5', active=True
        )
        self.move()
        other = Client.objects.create(name='Synthetic different owner', code='s5-other')
        AssetMachine.objects.filter(pk=self.machine.pk).update(client=other)
        with self.assertRaisesRegex(ValueError, 'placement'):
            current_expectation(namespace.pk, self.machine.pk)
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.client_id, other.pk)
        self.assertEqual(self.machine.physical_location_id, self.destination.pk)

    def test_internal_lookup_rejects_coerced_identifiers_before_database_use(self):
        """A boolean or string must not select an existing namespace or machine."""
        from integration_state.services import PlacementStateError, current_expectation

        namespace = SourceNamespace.objects.create(
            environment='synthetic-s5', active=True
        )
        self.move()
        for ns_id, source_id in (
            (True, self.machine.pk),
            (namespace.pk, True),
            (str(namespace.pk), self.machine.pk),
            (namespace.pk, str(self.machine.pk)),
            (0, self.machine.pk),
        ):
            with self.subTest(ns_id=ns_id, source_id=source_id):
                with self.assertRaises(PlacementStateError):
                    current_expectation(ns_id, source_id)

    def test_missing_baseline_is_typed_unqualified_and_disable_is_not_pending(self):
        """An incomplete scan does not imply empty, current or a fabricated marker."""
        from integration_state.models import SourceState
        from integration_state.services import PlacementStateError, current_expectation

        namespace = SourceNamespace.objects.create(
            environment='synthetic-s5', active=True
        )
        with self.assertRaises(PlacementStateError):
            current_expectation(namespace.pk, self.machine.pk)
        self.assertFalse(SourceState.objects.exists())
        self.move()
        self.assertIsNotNone(current_expectation(namespace.pk, self.machine.pk))
        SourceNamespace.objects.filter(pk=namespace.pk).update(active=False)
        self.assertIsNone(current_expectation(namespace.pk, self.machine.pk))
        self.assertEqual(SourceState.objects.count(), 1)


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class CommittedPlacementServiceTests(NativePlacementFixture, TransactionTestCase):
    """Use actual commit and a new database session, not TestCase savepoints."""

    def test_committed_transfer_survives_connection_close_and_independent_readback(
        self,
    ):
        """The durable expectation, receipt and native history outlive a connection."""
        from assets.models import MachineLocationTransfer, MachinePlacementHistory
        from integration_state.services import current_expectation

        self.assertFalse(connection.in_atomic_block)
        namespace = SourceNamespace.objects.create(
            environment='synthetic-s5', active=True
        )
        self.move()
        first = current_expectation(namespace.pk, self.machine.pk)
        previous_connection = connection.connection
        connection.close()
        self.assertIsNone(connection.connection)
        second = current_expectation(namespace.pk, self.machine.pk)
        self.assertIsNot(connection.connection, previous_connection)
        self.assertEqual(second, first)
        self.assertEqual(second.native_version, 1)
        self.assertEqual(MachineLocationTransfer.objects.count(), 1)
        self.assertEqual(MachinePlacementHistory.objects.count(), 1)


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class EnrollmentServiceTests(NativePlacementFixture, TestCase):
    """Baseline is bounded, source-owned and inactive until verified complete."""

    def test_existing_population_is_baselined_in_bounded_persisted_batches(self):
        """Two pages establish current expectations without touching native rows."""
        from assets.models import AssetMachine
        from integration_state.models import SourceChange, SourceState
        from integration_state.services import (
            activate_namespace,
            bootstrap_projection,
            current_expectation,
            prepare_projection,
        )

        AssetMachine.objects.create(
            name='Synthetic preexisting second', client=self.owner
        )
        before = list(
            AssetMachine.objects.order_by('pk').values_list(
                'pk',
                'client_id',
                'physical_location_id',
                'placement_version',
                'updated_at',
            )
        )
        namespace = activate_namespace('synthetic-s5')
        enrollment = prepare_projection(
            namespace.pk, 'synthetic-projection', self.owner.pk
        )
        self.assertFalse(enrollment.active)
        self.assertFalse(SourceState.objects.exists())
        first = bootstrap_projection(enrollment.pk, batch_size=1)
        enrollment.refresh_from_db()
        self.assertEqual(first, {'processed': 1, 'complete': False})
        self.assertFalse(enrollment.active)
        self.assertEqual(SourceState.objects.count(), 1)
        self.assertGreater(enrollment.bootstrap_cursor, 0)
        second = bootstrap_projection(enrollment.pk, batch_size=1)
        enrollment.refresh_from_db()
        self.assertEqual(second, {'processed': 1, 'complete': True})
        self.assertTrue(enrollment.active)
        self.assertTrue(enrollment.bootstrapped)
        self.assertEqual(SourceState.objects.count(), 2)
        self.assertEqual(SourceChange.objects.filter(operation='baseline').count(), 2)
        expected = current_expectation(namespace.pk, self.machine.pk)
        self.assertEqual((expected.native_version, expected.fence), (0, 1))
        self.assertEqual(
            list(
                AssetMachine.objects.order_by('pk').values_list(
                    'pk',
                    'client_id',
                    'physical_location_id',
                    'placement_version',
                    'updated_at',
                )
            ),
            before,
        )

    def test_final_baseline_checks_prior_pages_and_persists_failure(self):
        """Reaching the last key is not evidence that every source marker exists."""
        from assets.models import AssetMachine
        from integration_state.models import SourceState
        from integration_state.services import (
            PlacementStateError,
            activate_namespace,
            bootstrap_projection,
            prepare_projection,
        )

        AssetMachine.objects.create(name='Synthetic second page', client=self.owner)
        namespace = activate_namespace('synthetic-s5')
        enrollment = prepare_projection(
            namespace.pk, 'synthetic-projection', self.owner.pk
        )
        bootstrap_projection(enrollment.pk, batch_size=1)
        enrollment.refresh_from_db()
        saved_cursor = enrollment.bootstrap_cursor
        SourceState.objects.filter(source_id=self.machine.pk).delete()
        with self.assertRaises(PlacementStateError):
            bootstrap_projection(enrollment.pk, batch_size=1)
        enrollment.refresh_from_db()
        self.assertFalse(enrollment.active)
        self.assertFalse(enrollment.bootstrapped)
        self.assertEqual(enrollment.bootstrap_status, 'failed')
        self.assertEqual(enrollment.bootstrap_cursor, saved_cursor)

    def test_namespace_disable_reenable_rotates_epoch_and_preserves_native_writes(self):
        """A capture gap cannot inherit any old configuration or placement proof."""
        from integration_state.models import SourceState
        from integration_state.services import (
            PlacementStateError,
            activate_namespace,
            bootstrap_projection,
            current_expectation,
            disable_namespace,
            prepare_projection,
        )

        namespace = activate_namespace('synthetic-s5')
        old_epoch = namespace.epoch
        old = prepare_projection(namespace.pk, 'synthetic-projection', self.owner.pk)
        bootstrap_projection(old.pk)
        before = current_expectation(namespace.pk, self.machine.pk)
        disable_namespace(namespace.pk)
        self.move()
        self.assertIsNone(current_expectation(namespace.pk, self.machine.pk))
        self.assertEqual(
            SourceState.objects.get(epoch=old_epoch).revision, before.fence
        )
        resumed = activate_namespace('synthetic-s5')
        self.assertNotEqual(resumed.epoch, old_epoch)
        with self.assertRaises(PlacementStateError):
            bootstrap_projection(old.pk)
        new = prepare_projection(resumed.pk, 'synthetic-projection', self.owner.pk)
        self.assertNotEqual(new.epoch, old.epoch)
        bootstrap_projection(new.pk)
        expected = current_expectation(resumed.pk, self.machine.pk)
        self.assertEqual(expected.native_version, 1)
        self.assertEqual(expected.location.pk, self.destination.pk)
        self.assertEqual(expected.namespace_epoch, resumed.epoch)
        self.assertEqual(SourceState.objects.count(), 2)

    def test_disabled_projection_cannot_reactivate_without_new_configuration(self):
        """Disabling during a partial baseline is distinct from retryable failure."""
        from assets.models import AssetMachine
        from integration_state.services import (
            PlacementStateError,
            activate_namespace,
            bootstrap_projection,
            disable_projection,
            prepare_projection,
        )

        AssetMachine.objects.create(name='Synthetic partial second', client=self.owner)
        namespace = activate_namespace('synthetic-s5')
        old = prepare_projection(namespace.pk, 'synthetic-projection', self.owner.pk)
        bootstrap_projection(old.pk, batch_size=1)
        disable_projection(old.pk)
        with self.assertRaises(PlacementStateError):
            bootstrap_projection(old.pk)
        old.refresh_from_db()
        self.assertFalse(old.active)
        self.assertEqual(old.bootstrap_status, 'disabled')
        new = prepare_projection(namespace.pk, 'synthetic-projection', self.owner.pk)
        self.assertNotEqual(new.epoch, old.epoch)
        self.assertTrue(bootstrap_projection(new.pk)['complete'])
        with self.assertRaises(PlacementStateError):
            bootstrap_projection(old.pk)

    def test_admin_then_legacy_scope_change_retains_both_sides_without_repair(self):
        """Actual form/save and detail-view composition bypasses native versioning."""
        from django.contrib import admin
        from django.test import RequestFactory

        from rest_framework.test import APIRequestFactory, force_authenticate

        from assets.api import AssetMachineDetail
        from assets.models import AssetMachine, Client, MachinePlacementHistory
        from integration_state.models import SourceChange, SourceState
        from integration_state.services import (
            PlacementStateError,
            activate_namespace,
            bootstrap_projection,
            current_expectation,
            prepare_projection,
        )

        namespace = activate_namespace('synthetic-s5')
        enrollment = prepare_projection(
            namespace.pk, 'synthetic-projection', self.owner.pk
        )
        bootstrap_projection(enrollment.pk)
        other = Client.objects.create(name='Synthetic next owner', code='s5-next')
        model_admin = admin.site._registry[AssetMachine]
        request = RequestFactory().get('/admin/assets/assetmachine/')
        request.user = self.actor
        self.assertTrue(model_admin.has_change_permission(request, self.machine))
        form_type = model_admin.get_form(request, self.machine, change=True)
        form = form_type(
            {
                'name': self.machine.name,
                'asset_type': 'equipment',
                'client': self.owner.pk,
                'physical_location': self.destination.pk,
                'active': 'on',
                'source_context': '{}',
                'profile': '{}',
            },
            instance=self.machine,
        )
        self.assertTrue(form.is_valid(), form.errors)
        saved = model_admin.save_form(request, form, change=True)
        model_admin.save_model(request, saved, form, change=True)
        first = current_expectation(namespace.pk, self.machine.pk)
        self.assertEqual((first.native_version, first.fence), (0, 2))
        patch_request = APIRequestFactory().patch(
            '/api/assets/machines/', {'client': other.pk}, format='json'
        )
        force_authenticate(patch_request, user=self.actor)
        response = AssetMachineDetail.as_view()(patch_request, pk=self.machine.pk)
        self.assertEqual(response.status_code, 200)
        change = SourceChange.objects.order_by('pk').last()
        self.assertEqual(change.previous['client_id'], self.owner.pk)
        self.assertEqual(change.current['client_id'], other.pk)
        self.assertEqual(SourceState.objects.get().revision, 3)
        with self.assertRaises(PlacementStateError):
            current_expectation(namespace.pk, self.machine.pk)
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.client_id, other.pk)
        self.assertEqual(self.machine.physical_location_id, self.destination.pk)
        self.assertEqual(self.machine.placement_version, 0)
        self.assertFalse(MachinePlacementHistory.objects.exists())

    def test_real_population_adoption_placement_replay_and_dry_run_are_captured(self):
        """Synthetic native workbook carriers exercise apply, not a transfer stub."""
        from hashlib import sha256

        from assets.models import MachineLocationTransfer, MachinePlacementHistory
        from assets.population import apply_population
        from assets.population_workbook import (
            LocationRecord,
            MachineRecord,
            PopulationWorkbook,
        )
        from integration_state.models import SourceChange, SourceState
        from integration_state.services import activate_namespace, current_expectation

        namespace = activate_namespace('synthetic-s5')
        self.machine.client = None
        self.machine.save(update_fields=['client'])
        book = PopulationWorkbook(
            path='synthetic-s5.xlsx',
            sha256=sha256(b'synthetic-s5-population').hexdigest(),
            client_codes=(self.owner.code,),
            locations=[
                LocationRecord(
                    row=2,
                    code=self.destination.code,
                    name=self.destination.name,
                    kind=self.destination.kind,
                    parent_code=None,
                    timezone=self.destination.timezone,
                    description=self.destination.description,
                    client_code=self.owner.code,
                    archived=False,
                    reason='Synthetic S5',
                )
            ],
            machines=[
                MachineRecord(
                    row=2,
                    key='synthetic-equipment',
                    name=self.machine.name,
                    asset_type='equipment',
                    location_code=self.destination.code,
                    legacy_location='',
                    manufacturer='',
                    model='',
                    serial='',
                    description='',
                    active=True,
                    client_code=self.owner.code,
                    source_key='',
                    source_namespace='',
                    source_entity_uuid=None,
                    criticality='',
                    maintenance_strategy='',
                    entry_status='UI_API',
                )
            ],
        )
        dry = apply_population(book, self.actor, dry_run=True)
        self.assertTrue(dry['dry_run'])
        self.machine.refresh_from_db()
        self.assertIsNone(self.machine.client_id)
        self.assertIsNone(self.machine.physical_location_id)
        self.assertEqual(SourceState.objects.get().revision, 1)
        self.assertEqual(SourceChange.objects.count(), 1)
        self.assertFalse(MachineLocationTransfer.objects.exists())
        result = apply_population(book, self.actor)
        self.assertEqual(result['placements']['moved'], 1)
        expected = current_expectation(namespace.pk, self.machine.pk)
        self.assertEqual((expected.native_version, expected.fence), (1, 3))
        adoption = SourceChange.objects.get(revision=2)
        self.assertIsNone(adoption.previous['client_id'])
        self.assertEqual(adoption.current['client_id'], self.owner.pk)
        replay = apply_population(book, self.actor)
        self.assertEqual(replay['placements']['moved'], 0)
        self.assertEqual(current_expectation(namespace.pk, self.machine.pk), expected)
        self.assertEqual(SourceChange.objects.count(), 3)
        self.assertEqual(MachineLocationTransfer.objects.count(), 1)
        self.assertEqual(MachinePlacementHistory.objects.count(), 1)

    def test_unqualified_router_is_refused_before_any_enrollment_write(self):
        """Configured unqualified routing cannot execute an integration query."""
        from integration_state.models import ProjectionEnrollment
        from integration_state.services import PlacementStateError, activate_namespace

        class UnqualifiedRouter:
            """Django may initialize this fixture but must never route its query."""

            def db_for_read(self, *args, **kwargs):
                """Fail if integration ORM work precedes routing admission."""
                raise AssertionError('Unqualified router was invoked')

            db_for_write = db_for_read

        with override_settings(DATABASE_ROUTERS=[UnqualifiedRouter()]):
            with self.assertRaises(PlacementStateError):
                activate_namespace('synthetic-s5')
        self.assertFalse(SourceNamespace.objects.exists())
        self.assertFalse(ProjectionEnrollment.objects.exists())

    def test_failed_baseline_resumes_after_source_relationship_is_corrected(self):
        """Retry verifies native repair without rewriting it or forgetting failure."""
        from assets.location_models import AssetLocation
        from assets.models import AssetMachine, Client
        from integration_state.services import (
            PlacementStateError,
            activate_namespace,
            bootstrap_projection,
            current_expectation,
            prepare_projection,
        )

        other = Client.objects.create(name='Synthetic invalid owner', code='s5-invalid')
        foreign = AssetLocation.objects.create(
            client=other,
            name='Synthetic foreign',
            code='foreign',
            name_key='foreign',
            sibling_key='root',
        )
        namespace = activate_namespace('synthetic-s5')
        AssetMachine.objects.filter(pk=self.machine.pk).update(
            physical_location=foreign
        )
        enrollment = prepare_projection(
            namespace.pk, 'synthetic-projection', self.owner.pk
        )
        with self.assertRaises(PlacementStateError):
            bootstrap_projection(enrollment.pk)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.bootstrap_status, 'failed')
        self.assertEqual(enrollment.bootstrap_cursor, 0)
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.physical_location_id, foreign.pk)
        AssetMachine.objects.filter(pk=self.machine.pk).update(physical_location=None)
        self.assertTrue(bootstrap_projection(enrollment.pk)['complete'])
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.bootstrap_status, 'ready')
        self.assertTrue(enrollment.active)
        expected = current_expectation(namespace.pk, self.machine.pk)
        self.assertIsNone(expected.location)
        self.assertEqual((expected.native_version, expected.fence), (0, 2))
