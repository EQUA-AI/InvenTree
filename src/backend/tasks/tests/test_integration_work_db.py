"""Database-backed boundary tests for minimal Work source records."""

from dataclasses import fields

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from assets.models import AssetMachine, Client, ClientScopeGrant
from assets.tests.integration_attribution_test_support import (
    assume_synthetic_attribution,
)
from company.models import Company
from tasks.integration_sources import read_work_order
from tasks.models import WorkOrder
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='unavailable-in-test',
)
@assume_synthetic_attribution
class WorkSourceDatabaseTests(TestCase):
    """Use native Work scoping with synthetic clients and records."""

    def test_machine_only_work_has_minimal_projection_and_shared_is_withheld(self):
        """Preserve the projection positive without inferring shared attribution."""
        actor = get_user_model().objects.create_user(username='work-source-reader')
        group = Group.objects.create(name='Work source test readers')
        actor.groups.add(group)
        rule, _ = RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        own = Client.objects.create(name='Synthetic own', code='work-source-own')
        foreign = Client.objects.create(
            name='Synthetic foreign', code='work-source-foreign'
        )
        ClientScopeGrant.objects.create(user=actor, client=own)
        own_machine = AssetMachine.objects.create(name='Own machine', client=own)
        foreign_machine = AssetMachine.objects.create(
            name='Foreign machine', client=foreign
        )
        customer = Company.objects.create(name='Synthetic customer', is_customer=True)
        shared = WorkOrder.objects.create(
            title='Shared synthetic job',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=own_machine,
            customer=customer,
        )
        own_work = WorkOrder.objects.create(
            title='Machine-only synthetic job',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=own_machine,
        )
        private = WorkOrder.objects.create(
            title='Foreign machine-only job',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=foreign_machine,
        )
        self.assertIsNone(read_work_order(actor, environment='test', work_pk=shared.pk))
        result = read_work_order(actor, environment='test', work_pk=own_work.pk)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.identity.pk, own_work.pk)
        self.assertEqual(result.title, own_work.title)
        self.assertEqual(result.status, own_work.status)
        self.assertEqual(result.schema_version, 'aimms.work.basic.v1')
        self.assertEqual(
            {field.name for field in fields(result)},
            {'identity', 'title', 'status', 'schema_version'},
        )
        self.assertIsNone(
            read_work_order(actor, environment='test', work_pk=private.pk)
        )
        rule.can_view = False
        rule.save(update_fields=['can_view'])
        self.assertIsNone(
            read_work_order(actor, environment='test', work_pk=own_work.pk)
        )
