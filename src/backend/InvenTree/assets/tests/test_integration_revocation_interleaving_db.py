"""Sequential revocation controls for the unwired native source readers.

These tests exercise two source checks against a real synthetic database within
one test transaction. They do not establish cross-transaction atomicity.
"""

from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from tasks import scope as native_scope
from tasks.integration_sources import read_work_order
from tasks.models import WorkOrder

from assets.integration_sources import read_machine, read_maintenance_record
from assets.models import AssetMachine, AssetMaintenanceRecord, Client, ClientScopeGrant
from assets.tests.integration_attribution_test_support import (
    assume_synthetic_attribution,
)
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-interleaving-fallback',
)
@assume_synthetic_attribution
class SourceRevocationInterleavingTests(TestCase):
    """Remove an explicit grant between two sequential native scope checks."""

    def setUp(self):
        """Create a synthetic Work viewer and one machine-scoped record of each kind."""
        self.actor = get_user_model().objects.create_user(
            username='interleaving-reader'
        )
        group = Group.objects.create(name='Interleaving source readers')
        self.actor.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        client = Client.objects.create(
            name='Synthetic interleaving', code='interleaving'
        )
        self.grant = ClientScopeGrant.objects.create(user=self.actor, client=client)
        self.machine = AssetMachine.objects.create(
            name='Synthetic interleaving machine', client=client
        )
        self.maintenance = AssetMaintenanceRecord.objects.create(
            machine=self.machine, date=date(2020, 1, 2), summary='Synthetic service'
        )
        self.work = WorkOrder.objects.create(
            title='Synthetic machine-only job',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=self.machine,
        )

    def _revoke_on_second_call(self, original):
        calls = []

        def resolve(actor):
            calls.append(actor.pk)
            if len(calls) == 2:
                self.grant.delete()
            return original(actor)

        return calls, resolve

    def test_machine_grant_revocation_between_checks_withholds_record(self):
        """The second machine check sees a just-removed explicit client grant."""
        self.assertIsNotNone(
            read_machine(self.actor, environment='test', machine_pk=self.machine.pk)
        )
        calls, resolver = self._revoke_on_second_call(native_scope.scoped_machines)
        with patch('tasks.scope.scoped_machines', side_effect=resolver):
            result = read_machine(
                self.actor, environment='test', machine_pk=self.machine.pk
            )
        self.assertEqual(calls, [self.actor.pk, self.actor.pk])
        self.assertIsNone(result)

    def test_maintenance_grant_revocation_between_checks_withholds_record(self):
        """The second maintenance check sees a just-removed client grant."""
        self.assertIsNotNone(
            read_maintenance_record(
                self.actor, environment='test', record_pk=self.maintenance.pk
            )
        )
        calls, resolver = self._revoke_on_second_call(
            native_scope.maintenance_record_scope_filter
        )
        with patch('tasks.scope.maintenance_record_scope_filter', side_effect=resolver):
            result = read_maintenance_record(
                self.actor, environment='test', record_pk=self.maintenance.pk
            )
        self.assertEqual(calls, [self.actor.pk, self.actor.pk])
        self.assertIsNone(result)

    def test_machine_only_work_revocation_between_checks_withholds_record(self):
        """The second Work check sees a just-removed client grant."""
        self.assertIsNotNone(
            read_work_order(self.actor, environment='test', work_pk=self.work.pk)
        )
        calls, resolver = self._revoke_on_second_call(native_scope.scoped_work_orders)
        with patch('tasks.scope.scoped_work_orders', side_effect=resolver):
            result = read_work_order(
                self.actor, environment='test', work_pk=self.work.pk
            )
        self.assertEqual(calls, [self.actor.pk, self.actor.pk])
        self.assertIsNone(result)
