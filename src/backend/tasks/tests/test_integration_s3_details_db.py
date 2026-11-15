"""S3 direct details controls, not historical attribution qualification."""

import importlib
import importlib.util
from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings

from assets.models import AssetMaintenanceRecord, ClientScopeGrant
from assets.tests.integration_attribution_test_support import (
    assume_synthetic_attribution,
)
from tasks.integration_sources import read_work_order
from tasks.tests.test_integration_s3_document_metadata_db import DocumentFixture


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-s3-test-client',
)
@assume_synthetic_attribution
class SimulatedDirectDetailsTests(DocumentFixture, TestCase):
    """Exercise downstream field semantics with attribution explicitly simulated."""

    def read_work_details(self):
        """Characterize minimal Work until the additive details operation exists."""
        module = 'tasks.integration_s3_details'
        reader = (
            importlib.import_module(module).read_work_details
            if importlib.util.find_spec(module)
            else read_work_order
        )
        return reader(self.actor, environment='s3-test', work_pk=self.work.pk)

    def test_work_details_preserve_distinct_status_nulls_and_source_clocks(self):
        """Lifecycle, estimates, dates and source clocks cannot collapse into status."""
        result = self.read_work_details()
        self.assertIsNotNone(result)
        self.assertEqual(
            getattr(result, 'lifecycle_status', None), self.work.lifecycle_status
        )
        self.assertEqual(result.status, self.work.status)
        self.assertEqual(result.description, '')
        self.assertIsNone(result.due_date)
        self.assertIsNone(result.estimated_minutes)
        self.assertIsNone(result.actual_completed_at)
        self.assertEqual(result.created_at, self.work.created_at)
        self.assertEqual(result.updated_at, self.work.updated_at)
        self.assertEqual(result.lifecycle_version, self.work.lifecycle_version)
        self.assertFalse(hasattr(result, 'assigned_to'))
        self.assertFalse(hasattr(result, 'machine'))
        self.assertIn('machine', result.withheld_fields)
        from tasks.integration_s3_details import WORK_FIELDS

        for name in WORK_FIELDS:
            with self.subTest(field=name):
                self.assertEqual(getattr(result, name), getattr(self.work, name))

    def test_native_basic_work_schema_remains_unchanged(self):
        """Additive details must not alter the original basic source shape."""
        result = read_work_order(
            self.actor, environment='s3-test', work_pk=self.work.pk
        )
        self.assertEqual(result.schema_version, 'aimms.work.basic.v1')
        self.assertFalse(hasattr(result, 'lifecycle_status'))

    def test_maintenance_details_do_not_expand_people_or_work_authority(self):
        """Preserve recorded date/details while withholding separately governed fields."""
        from assets.integration_sources import read_maintenance_record
        from tasks import integration_s3_details as details

        record = AssetMaintenanceRecord.objects.create(
            machine=self.machine,
            date=date(2020, 1, 2),
            summary='Synthetic history',
            details='Synthetic recorded detail',
            performed_by='Private technician',
        )
        reader = getattr(details, 'read_maintenance_details', read_maintenance_record)
        result = reader(self.actor, environment='s3-test', record_pk=record.pk)
        self.assertIsNotNone(result)
        self.assertEqual(getattr(result, 'details', None), record.details)
        self.assertEqual(result.date, record.date)
        self.assertEqual(result.created_at, record.created_at)
        self.assertEqual(result.updated_at, record.updated_at)
        self.assertFalse(hasattr(result, 'work_order'))
        self.assertFalse(hasattr(result, 'performed_by'))
        self.assertIn('performed_by', result.withheld_fields)
        self.assertEqual(
            read_maintenance_record(
                self.actor, environment='s3-test', record_pk=record.pk
            ).schema_version,
            'aimms.maintenance.basic.v1',
        )

    def test_work_revocation_during_projection_withholds_result(self):
        """A prior valid observation cannot authorize final emission."""
        from tasks import integration_s3_details as details

        self.assertIsNotNone(self.read_work_details())
        original = details._project_work

        def revoked(*args):
            result = original(*args)
            self.grant.delete()
            return result

        with patch.object(details, '_project_work', side_effect=revoked):
            self.assertIsNone(self.read_work_details())

    def test_direct_update_without_version_change_is_detected(self):
        """Command counters alone cannot bind every native or alternate writer."""
        from tasks import integration_s3_details as details
        from tasks.models import WorkOrder

        before = self.read_work_details()
        original = details._project_work

        def changed(*args):
            result = original(*args)
            WorkOrder.objects.filter(pk=self.work.pk).update(
                description='Changed directly'
            )
            return result

        with patch.object(details, '_project_work', side_effect=changed):
            self.assertIsNone(self.read_work_details())
        after = self.read_work_details()
        self.assertEqual(before.lifecycle_version, after.lifecycle_version)
        self.assertEqual(before.updated_at, after.updated_at)
        self.assertNotEqual(before.description, after.description)

    def test_invalid_payload_is_not_successful_empty(self):
        """An authorized missing revision remains an explicit source failure."""
        from tasks import integration_s3_details as details

        original = details._work_snapshot

        def invalid(pk):
            row = original(pk)
            row['lifecycle_version'] = None
            return row

        error = getattr(details, 'DirectDetailsUnavailableError', ValueError)
        with patch.object(details, '_work_snapshot', side_effect=invalid):
            with self.assertRaises(error):
                self.read_work_details()

    def test_invalid_payload_after_revocation_does_not_leak_failure(self):
        """Recheck failure carriers as well as successful fields."""
        from tasks import integration_s3_details as details

        self.assertIsNotNone(self.read_work_details())
        original = details._project_work

        def revoked(identity, row):
            self.grant.delete()
            return original(identity, dict(row, lifecycle_version=None))

        with patch.object(details, '_project_work', side_effect=revoked):
            try:
                result = self.read_work_details()
            except ValueError:
                self.fail('A source failure escaped after authority was revoked')
        self.assertIsNone(result)

    def test_narrow_native_scope_is_not_replaced_by_explicit_grant(self):
        """Independently deny native scope while the real client grant remains."""
        from tasks.models import WorkOrder

        self.assertIsNotNone(self.read_work_details())
        with patch(
            'tasks.scope.scoped_work_orders', return_value=WorkOrder.objects.none()
        ):
            self.assertTrue(ClientScopeGrant.objects.filter(pk=self.grant.pk).exists())
            self.assertIsNone(self.read_work_details())

    def test_explicit_grant_is_not_replaced_by_native_scope(self):
        """Independently deny integration grants with real native visibility positive."""
        from tasks.scope import scoped_work_orders

        self.assertIsNotNone(self.read_work_details())
        with patch(
            'assets.integration_authority.explicitly_granted_client_ids',
            return_value=[],
        ):
            self.assertTrue(
                scoped_work_orders(self.actor).filter(pk=self.work.pk).exists()
            )
            self.assertIsNone(self.read_work_details())


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-s3-test-client',
)
class ActualPolicyDirectDetailsTests(DocumentFixture, TestCase):
    """Real proof withholding, not isolated positive authorization evidence."""

    def test_unproven_work_never_reaches_details_projection(self):
        """Native visibility and a real current grant do not prove historical binding."""
        from tasks import integration_s3_details as details
        from tasks.scope import scoped_work_orders

        self.assertTrue(scoped_work_orders(self.actor).filter(pk=self.work.pk).exists())
        with patch.object(details, '_work_snapshot') as snapshot:
            self.assertIsNone(
                details.read_work_details(
                    self.actor, environment='s3-test', work_pk=self.work.pk
                )
            )
        snapshot.assert_not_called()

    def test_unproven_maintenance_never_reaches_details_projection(self):
        """No maintenance, people or linked-Work details are projected without proof."""
        from tasks import integration_s3_details as details
        from tasks.scope import maintenance_record_scope_filter

        record = AssetMaintenanceRecord.objects.create(
            machine=self.machine, date=date(2020, 1, 2), summary='Synthetic history'
        )
        self.assertTrue(
            AssetMaintenanceRecord.objects.filter(
                maintenance_record_scope_filter(self.actor), pk=record.pk
            ).exists()
        )
        with patch.object(details, '_maintenance_snapshot') as snapshot:
            self.assertIsNone(
                details.read_maintenance_details(
                    self.actor, environment='s3-test', record_pk=record.pk
                )
            )
        snapshot.assert_not_called()
