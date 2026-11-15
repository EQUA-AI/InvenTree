"""Focused S3 maintenance-edge contracts; historical positives are simulated."""

from dataclasses import FrozenInstanceError
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from tasks.integration_sources import WorkSourceIdentity
from tasks.models import WorkOrder

from assets import integration_work_links as links
from assets.integration_contracts import MaintenanceSourceIdentity
from assets.models import AssetMaintenanceRecord, ClientScopeGrant
from assets.tests import test_integration_maintenance_work_link_db as original
from assets.tests.integration_attribution_test_support import (
    assume_synthetic_attribution,
)
from company.models import Company


class LinkContractTests(SimpleTestCase):
    """A frozen outer record cannot make a malformed nested identity valid."""

    def test_equal_primary_keys_remain_distinct_domains(self):
        """The same integer is not a shared Work/maintenance namespace."""
        maintenance = MaintenanceSourceIdentity(' Literal/站 ', 7)
        work = WorkSourceIdentity(' Literal/站 ', 7)
        link = links.MaintenanceWorkLinkSourceRecord(maintenance, 'available', work)
        self.assertNotEqual(link.maintenance, link.work)
        self.assertEqual(link.work.environment, ' Literal/站 ')
        self.assertEqual(link.schema_version, 'aimms.maintenance.work_link.v1')
        with self.assertRaises(FrozenInstanceError):
            link.state = 'none'

    def test_malformed_nested_coordinates_are_revalidated(self):
        """A corrupted frozen carrier cannot reuse bool/string/missing coordinates."""
        for domain in (MaintenanceSourceIdentity, WorkSourceIdentity):
            for attribute, value in (
                ('pk', True),
                ('pk', '1'),
                ('pk', 0),
                ('environment', ''),
                ('environment', None),
            ):
                with self.subTest(
                    domain=domain.__name__, attribute=attribute, value=value
                ):
                    maintenance = MaintenanceSourceIdentity('source', 1)
                    work = WorkSourceIdentity('source', 1)
                    subject = (
                        maintenance if domain is MaintenanceSourceIdentity else work
                    )
                    object.__setattr__(subject, attribute, value)
                    with self.assertRaises(ValueError):
                        links.MaintenanceWorkLinkSourceRecord(
                            maintenance, 'available', work
                        )

    def test_nested_identity_subclasses_are_not_accepted(self):
        """The declared identity domains are exact carriers, not extension hooks."""

        class WorkSubclass(WorkSourceIdentity):
            pass

        class MaintenanceSubclass(MaintenanceSourceIdentity):
            pass

        for maintenance, work in (
            (MaintenanceSubclass('source', 1), WorkSourceIdentity('source', 1)),
            (MaintenanceSourceIdentity('source', 1), WorkSubclass('source', 1)),
        ):
            with self.subTest(
                maintenance=type(maintenance).__name__, work=type(work).__name__
            ):
                with self.assertRaises(ValueError):
                    links.MaintenanceWorkLinkSourceRecord(
                        maintenance, 'available', work
                    )

    def test_unknown_schema_is_not_a_supported_edge(self):
        """Revalidation must not certify a record under an altered schema marker."""
        link = links.MaintenanceWorkLinkSourceRecord(
            MaintenanceSourceIdentity('source', 1), 'none', None
        )
        object.__setattr__(link, 'schema_version', 'unrecognized-edge-version')
        with self.assertRaises(ValueError):
            links.MaintenanceWorkLinkSourceRecord.__post_init__(link)


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='unavailable-in-test',
)
@assume_synthetic_attribution
class SimulatedFinalLinkTests(TestCase):
    """Borrow only fixture methods, not the original class's test inventory."""

    setUp = original.MaintenanceWorkLinkTests.setUp.__wrapped__
    make_work = original.MaintenanceWorkLinkTests.make_work
    read = original.MaintenanceWorkLinkTests.read

    def test_each_state_rechecks_source_after_final_construction(self):
        """Availability, unknown and absence are all withheld after source revocation."""
        customer = Company.objects.create(name='S3 synthetic shared', is_customer=True)
        original_post = links.MaintenanceWorkLinkSourceRecord.__post_init__
        for state in ('available', 'none', 'unknown'):
            with self.subTest(state=state):
                WorkOrder.objects.filter(pk=self.work.pk).update(
                    customer=customer if state == 'unknown' else None
                )
                AssetMaintenanceRecord.objects.filter(pk=self.record.pk).update(
                    work_order=None if state == 'none' else self.work
                )
                self.assertEqual(self.read().state, state)

                def revoked(result):
                    original_post(result)
                    self.grant.delete()

                try:
                    with patch.object(
                        links.MaintenanceWorkLinkSourceRecord,
                        '__post_init__',
                        new=revoked,
                    ):
                        self.assertIsNone(self.read())
                finally:
                    self.grant = ClientScopeGrant.objects.create(
                        user=self.actor, client=self.own
                    )
                self.assertEqual(self.read().state, state)

    def test_constructor_fault_after_source_revocation_is_withheld(self):
        """A source validation failure cannot outlive its underlying authorization."""
        self.assertEqual(self.read().state, 'available')
        original_post = links.MaintenanceWorkLinkSourceRecord.__post_init__

        def failed(result):
            original_post(result)
            self.grant.delete()
            raise ValueError('Synthetic private invalid edge')

        with patch.object(
            links.MaintenanceWorkLinkSourceRecord, '__post_init__', new=failed
        ):
            try:
                self.assertIsNone(self.read())
            except ValueError:
                self.fail('Edge validation failure escaped after source revocation')

    def test_target_revoked_during_final_construction_withholds_link(self):
        """A still-authorized maintenance source cannot authorize a revoked target."""
        self.assertEqual(self.read().state, 'available')
        original_post = links.MaintenanceWorkLinkSourceRecord.__post_init__
        customer = Company.objects.create(
            name='S3 synthetic target revocation', is_customer=True
        )

        def revoked(result):
            original_post(result)
            WorkOrder.objects.filter(pk=self.work.pk).update(customer=customer)

        with patch.object(
            links.MaintenanceWorkLinkSourceRecord, '__post_init__', new=revoked
        ):
            self.assertIsNone(self.read())
        self.assertEqual(self.read().state, 'unknown')

    def test_newly_available_target_does_not_emit_old_unknown_state(self):
        """A final projection cannot label a changed target observation as current."""
        customer = Company.objects.create(
            name='S3 synthetic target restoration', is_customer=True
        )
        WorkOrder.objects.filter(pk=self.work.pk).update(customer=customer)
        self.assertEqual(self.read().state, 'unknown')
        original_post = links.MaintenanceWorkLinkSourceRecord.__post_init__

        def restored(result):
            original_post(result)
            WorkOrder.objects.filter(pk=self.work.pk).update(customer=None)

        with patch.object(
            links.MaintenanceWorkLinkSourceRecord, '__post_init__', new=restored
        ):
            self.assertIsNone(self.read())
        self.assertEqual(self.read().state, 'available')
