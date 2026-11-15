"""Native placement-state persistence controls using only synthetic rows."""

from django.db import IntegrityError, transaction
from django.test import TestCase

from assets.models import AssetMachine, Client


class PlacementModelTests(TestCase):
    """Configuration absence is a useful native-write positive control."""

    def test_default_off_native_write_does_not_create_integration_state(self):
        """No namespace or enrollment is manufactured by an ordinary write."""
        from integration_state.models import SourceNamespace, SourceState

        client = Client.objects.create(name='Synthetic S5 owner', code='s5-model')
        machine = AssetMachine.objects.create(name='Synthetic machine', client=client)
        machine.refresh_from_db()
        self.assertEqual(machine.placement_version, 0)
        self.assertIsNone(machine.physical_location_id)
        self.assertFalse(SourceNamespace.objects.exists())
        self.assertFalse(SourceState.objects.exists())

    def test_native_application_registry_includes_state_owner(self):
        """The production app list, not only this fixture, registers the state app."""
        import ast
        from pathlib import Path

        path = Path(__file__).resolve().parents[2] / 'InvenTree/settings.py'
        tree = ast.parse(path.read_text())
        configured = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == 'INSTALLED_APPS'
                for target in node.targets
            ):
                configured = ast.literal_eval(node.value)
                break
        self.assertIsNotNone(configured)
        self.assertIn('integration_state.apps.IntegrationStateConfig', configured)

    def test_projection_and_ack_storage_do_not_implicitly_activate_a_reader(self):
        """Stored metadata alone is neither enrollment nor serving authority."""
        from integration_state.models import (
            PlacementAcknowledgement,
            ProjectionEnrollment,
            SourceNamespace,
        )

        namespace = SourceNamespace.objects.create(environment='synthetic-model-s5')
        enrollment = ProjectionEnrollment.objects.create(
            namespace=namespace,
            namespace_epoch=namespace.epoch,
            projection='synthetic-projection',
            client_id=3,
        )
        acknowledgement = PlacementAcknowledgement.objects.create(
            enrollment=enrollment,
            source_id=7,
            namespace_epoch=namespace.epoch,
            source_revision=1,
            client_id=3,
            location_id=None,
            native_version=0,
            serving_generation='synthetic-g1',
        )
        enrollment.refresh_from_db()
        acknowledgement.refresh_from_db()
        self.assertFalse(enrollment.active)
        self.assertFalse(enrollment.bootstrapped)
        self.assertFalse(enrollment.generation_known)
        self.assertIsNone(enrollment.served_generation)
        self.assertEqual(enrollment.observation_revision, 0)
        self.assertEqual(acknowledgement.source_revision, 1)
        self.assertEqual(acknowledgement.serving_generation, 'synthetic-g1')

    def test_duplicate_active_projection_identity_is_rejected_by_database(self):
        """Two configuration epochs cannot both own the same active population."""
        from integration_state.models import ProjectionEnrollment, SourceNamespace

        namespace = SourceNamespace.objects.create(environment='synthetic-unique')
        values = {
            'namespace': namespace,
            'namespace_epoch': namespace.epoch,
            'projection': 'synthetic-projection',
            'client_id': 3,
            'active': True,
            'bootstrapped': True,
            'bootstrap_status': 'ready',
        }
        ProjectionEnrollment.objects.create(**values)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProjectionEnrollment.objects.create(**values)

    def test_duplicate_accepted_source_identity_is_rejected_by_database(self):
        """Concurrent acknowledgement cannot create ambiguous accepted rows."""
        from integration_state.models import (
            PlacementAcknowledgement,
            ProjectionEnrollment,
            SourceNamespace,
        )

        namespace = SourceNamespace.objects.create(environment='synthetic-ack-unique')
        enrollment = ProjectionEnrollment.objects.create(
            namespace=namespace,
            namespace_epoch=namespace.epoch,
            projection='synthetic-projection',
            client_id=3,
        )
        values = {
            'enrollment': enrollment,
            'source_id': 7,
            'namespace_epoch': namespace.epoch,
            'source_revision': 1,
            'client_id': 3,
            'location_id': None,
            'native_version': 0,
            'serving_generation': 'synthetic-g1',
        }
        PlacementAcknowledgement.objects.create(**values)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PlacementAcknowledgement.objects.create(**values)

    def test_active_projection_requires_database_enforced_bootstrap_readiness(self):
        """Metadata cannot claim active readiness through an incomplete state."""
        from integration_state.models import ProjectionEnrollment, SourceNamespace

        namespace = SourceNamespace.objects.create(environment='synthetic-readiness')
        for projection, active, bootstrapped, status in (
            ('unbootstrapped', True, False, 'preparing'),
            ('failed-active', True, True, 'failed'),
            ('unknown-status', False, False, 'unknown'),
        ):
            with self.subTest(projection=projection):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    ProjectionEnrollment.objects.create(
                        namespace=namespace,
                        namespace_epoch=namespace.epoch,
                        projection=projection,
                        client_id=3,
                        active=active,
                        bootstrapped=bootstrapped,
                        bootstrap_status=status,
                    )
        valid = ProjectionEnrollment.objects.create(
            namespace=namespace,
            namespace_epoch=namespace.epoch,
            projection='ready-positive',
            client_id=3,
            active=True,
            bootstrapped=True,
            bootstrap_status='ready',
        )
        self.assertTrue(valid.active)
