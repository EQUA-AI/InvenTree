"""Contrast native fallback and stricter unwired source readers.

The owner requires explicit grants for the new integration; native behavior
remains unchanged. These synthetic tests do not qualify production behavior.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from tasks.integration_sources import read_work_order
from tasks.models import WorkOrder
from tasks.scope import maintenance_record_scope_filter, scoped_work_orders

from assets.integration_sources import read_maintenance_record
from assets.models import AssetMachine, AssetMaintenanceRecord, Client, ClientScopeGrant
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='cross-domain-fallback',
)
class SourceFallbackCrossDomainTests(TestCase):
    """Use synthetic clients and a configured, active fallback client."""

    def setUp(self):
        """Set one explicit grant, a Work role, and a fallback machine."""
        self.actor = get_user_model().objects.create_user(
            username='cross-domain-reader'
        )
        group = Group.objects.create(name='Cross-domain fallback readers')
        self.actor.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        explicit = Client.objects.create(
            name='Synthetic explicit', code='cross-domain-explicit'
        )
        fallback = Client.objects.create(
            name='Synthetic fallback', code='cross-domain-fallback'
        )
        self.grant = ClientScopeGrant.objects.create(user=self.actor, client=explicit)
        self.machine = AssetMachine.objects.create(
            name='Synthetic fallback asset', client=fallback
        )

    def test_native_maintenance_fallback_is_not_integration_authority(self):
        """Native history scope can fall back while the new reader withholds."""
        record = AssetMaintenanceRecord.objects.create(
            machine=self.machine, date=date(2020, 1, 2), summary='Synthetic service'
        )
        self.assertIsNone(
            read_maintenance_record(self.actor, environment='test', record_pk=record.pk)
        )
        self.grant.delete()
        self.assertIsNotNone(
            AssetMaintenanceRecord.objects.filter(
                maintenance_record_scope_filter(self.actor), pk=record.pk
            ).first()
        )
        self.assertIsNone(
            read_maintenance_record(self.actor, environment='test', record_pk=record.pk)
        )

    def test_native_machine_only_work_fallback_is_not_integration_authority(self):
        """Native machine-only Work scope can fall back while the reader withholds."""
        work = WorkOrder.objects.create(
            title='Synthetic machine-only job',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=self.machine,
        )
        self.assertIsNone(
            read_work_order(self.actor, environment='test', work_pk=work.pk)
        )
        self.grant.delete()
        self.assertIsNotNone(scoped_work_orders(self.actor).filter(pk=work.pk).first())
        self.assertIsNone(
            read_work_order(self.actor, environment='test', work_pk=work.pk)
        )
