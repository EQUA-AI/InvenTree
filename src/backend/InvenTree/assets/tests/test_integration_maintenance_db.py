"""Database-backed boundary tests for maintenance source records."""

from dataclasses import fields
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from assets.integration_sources import read_maintenance_record
from assets.models import AssetMachine, AssetMaintenanceRecord, Client, ClientScopeGrant
from assets.tests.integration_attribution_test_support import (
    assume_synthetic_attribution,
)
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='unavailable-in-test',
)
@assume_synthetic_attribution
class MaintenanceSourceDatabaseTests(TestCase):
    """Synthetic clients and a real maintenance-scope resolver."""

    def test_authorized_maintenance_record_and_foreign_record(self):
        """Return native maintenance fields only within current client scope."""
        actor = get_user_model().objects.create_user(username='maintenance-reader')
        group = Group.objects.create(name='Maintenance source test readers')
        actor.groups.add(group)
        rule, _ = RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        own = Client.objects.create(name='Synthetic own', code='maintenance-own')
        foreign = Client.objects.create(
            name='Synthetic foreign', code='maintenance-foreign'
        )
        grant = ClientScopeGrant.objects.create(user=actor, client=own)
        machine = AssetMachine.objects.create(name='Synthetic machine', client=own)
        other_machine = AssetMachine.objects.create(
            name='Synthetic foreign machine', client=foreign
        )
        record = AssetMaintenanceRecord.objects.create(
            machine=machine, date=date(2020, 1, 2), summary='Synthetic service'
        )
        other = AssetMaintenanceRecord.objects.create(
            machine=other_machine, date=date(2020, 1, 3), summary='Foreign service'
        )
        result = read_maintenance_record(actor, environment='test', record_pk=record.pk)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.identity.environment, 'test')
        self.assertEqual(result.identity.pk, record.pk)
        self.assertEqual(result.machine.pk, machine.pk)
        self.assertEqual(result.date, date(2020, 1, 2))
        self.assertEqual(result.summary, 'Synthetic service')
        self.assertEqual(result.schema_version, 'aimms.maintenance.basic.v1')
        self.assertEqual(
            {field.name for field in fields(result)},
            {'identity', 'machine', 'date', 'summary', 'schema_version'},
        )
        self.assertIsNone(
            read_maintenance_record(actor, environment='test', record_pk=other.pk)
        )
        self.assertIsNone(
            read_maintenance_record(actor, environment='test', record_pk=999999)
        )
        rule.can_view = False
        rule.save(update_fields=['can_view'])
        self.assertIsNone(
            read_maintenance_record(actor, environment='test', record_pk=record.pk)
        )
        rule.can_view = True
        rule.save(update_fields=['can_view'])
        self.assertIsNotNone(
            read_maintenance_record(actor, environment='test', record_pk=record.pk)
        )
        grant.delete()
        self.assertIsNone(
            read_maintenance_record(actor, environment='test', record_pk=record.pk)
        )
