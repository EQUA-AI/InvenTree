"""Fail-closed integration controls when historical attribution is unproven."""

import unittest
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from tasks.integration_attachments import (
    read_work_attachment,
    read_work_attachment_content,
)
from tasks.integration_sources import read_work_order
from tasks.models import WorkOrder
from tasks.scope import (
    maintenance_record_scope_filter,
    scoped_machines,
    scoped_work_orders,
)

from assets import integration_attribution
from assets.integration_sources import (
    read_machine,
    read_machine_attachment,
    read_machine_attachment_content,
    read_maintenance_record,
)
from assets.integration_work_links import read_maintenance_work_link
from assets.models import AssetMachine, AssetMaintenanceRecord, Client, ClientScopeGrant
from assets.tests.integration_attribution_test_support import (
    assume_synthetic_attribution,
)
from common.models import Attachment
from company.models import Company
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='unavailable-in-test',
)
class UnprovenAttributionTests(TestCase):
    """Native visibility and current grants are not historical provenance."""

    def setUp(self):
        """Create an authorized synthetic actor, client, machine and Work order."""
        self.actor = get_user_model().objects.create_user(username='attribution-reader')
        group = Group.objects.create(name='Attribution test readers')
        self.actor.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        self.client_a = Client.objects.create(name='Synthetic A', code='proof-a')
        ClientScopeGrant.objects.create(user=self.actor, client=self.client_a)
        self.machine = AssetMachine.objects.create(
            name='Synthetic', client=self.client_a
        )
        self.work = WorkOrder.objects.create(
            title='Unproven synthetic Work',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=self.machine,
        )
        self.maintenance = AssetMaintenanceRecord.objects.create(
            machine=self.machine,
            date=date(2020, 1, 2),
            summary='Unproven synthetic maintenance',
            work_order=self.work,
        )

    def attachment(self, owner_type, owner_pk):
        """Create synthetic uploaded-file metadata without touching storage."""
        item = Attachment(
            model_type=owner_type,
            model_id=owner_pk,
            link='https://example.invalid/synthetic',
        )
        item.save(rebuild=False)
        Attachment.objects.filter(pk=item.pk).update(attachment='synthetic.bin')
        return item

    def test_simulated_attribution_fixture_restores_real_filters(self):
        """The prototype-only fixture cannot leave admission enabled afterward."""
        names = (
            'attributed_work_orders',
            'attributed_maintenance_records',
            'attributed_machine_attachments',
        )
        originals = {name: getattr(integration_attribution, name) for name in names}

        @assume_synthetic_attribution
        class Probe(unittest.TestCase):
            def test_admission_is_simulated(self):
                rows = object()
                for name in names:
                    self.assertIs(getattr(integration_attribution, name)(rows), rows)

        result = unittest.TestResult()
        Probe('test_admission_is_simulated').run(result)
        self.assertTrue(result.wasSuccessful())
        for name in names:
            self.assertIs(getattr(integration_attribution, name), originals[name])

    def test_natively_visible_granted_work_without_attribution_is_withheld(self):
        """A currently granted machine cannot establish ownership of its Work."""
        self.assertTrue(scoped_work_orders(self.actor).filter(pk=self.work.pk).exists())
        self.assertIsNone(
            read_work_order(self.actor, environment='synthetic', work_pk=self.work.pk)
        )

    def test_natively_visible_shared_and_machine_less_work_is_withheld(self):
        """Future inclusion requires proven client attribution for shared Work."""
        customer = Company.objects.create(name='Synthetic customer', is_customer=True)
        for machine in (self.machine, None):
            work = WorkOrder.objects.create(
                title='Unproven shared Work',
                status=WorkOrder.STATUS_BACKLOG,
                priority=WorkOrder.PRIORITY_MEDIUM,
                machine=machine,
                customer=customer,
            )
            with self.subTest(machine_id=work.machine_id):
                self.assertTrue(
                    scoped_work_orders(self.actor).filter(pk=work.pk).exists()
                )
                self.assertIsNone(
                    read_work_order(
                        self.actor, environment='synthetic', work_pk=work.pk
                    )
                )

    def test_natively_visible_maintenance_without_attribution_is_withheld(self):
        """A current machine grant does not prove a maintenance event's client."""
        self.assertTrue(
            AssetMaintenanceRecord.objects.filter(
                maintenance_record_scope_filter(self.actor), pk=self.maintenance.pk
            ).exists()
        )
        self.assertIsNone(
            read_maintenance_record(
                self.actor, environment='synthetic', record_pk=self.maintenance.pk
            )
        )

    def test_unattributed_maintenance_link_does_not_disclose_existence(self):
        """A withheld source must not emit an absent or unknown link envelope."""
        self.assertTrue(
            AssetMaintenanceRecord.objects.filter(
                maintenance_record_scope_filter(self.actor), pk=self.maintenance.pk
            ).exists()
        )
        self.assertIsNone(
            read_maintenance_work_link(
                self.actor, environment='synthetic', record_pk=self.maintenance.pk
            )
        )

    def test_machine_attachment_without_attribution_is_withheld(self):
        """Machine metadata visibility does not attribute attached documents."""
        attachment = self.attachment('assetmachine', self.machine.pk)
        self.assertTrue(scoped_machines(self.actor).filter(pk=self.machine.pk).exists())
        self.assertTrue(AssetMachine.check_related_permission('view', self.actor))
        self.assertIsNotNone(
            read_machine(
                self.actor, environment='synthetic', machine_pk=self.machine.pk
            )
        )
        self.assertIsNone(
            read_machine_attachment(
                self.actor, environment='synthetic', attachment_pk=attachment.pk
            )
        )

    def test_unattributed_machine_attachment_never_opens_storage(self):
        """An unauthorized attachment identity cannot be used to read bytes."""
        attachment = self.attachment('assetmachine', self.machine.pk)
        with patch('django.core.files.storage.default_storage.open') as opened:
            self.assertIsNone(
                read_machine_attachment_content(
                    self.actor,
                    environment='synthetic',
                    attachment_pk=attachment.pk,
                    max_bytes=64,
                )
            )
        opened.assert_not_called()

    def test_unattributed_work_attachment_never_opens_storage(self):
        """Unproven Work cannot authorize attachment metadata or bytes."""
        attachment = self.attachment('workorder', self.work.pk)
        self.assertTrue(scoped_work_orders(self.actor).filter(pk=self.work.pk).exists())
        self.assertIsNone(
            read_work_attachment(
                self.actor, environment='synthetic', attachment_pk=attachment.pk
            )
        )
        with patch('django.core.files.storage.default_storage.open') as opened:
            self.assertIsNone(
                read_work_attachment_content(
                    self.actor,
                    environment='synthetic',
                    attachment_pk=attachment.pk,
                    max_bytes=64,
                )
            )
        opened.assert_not_called()

    def test_transferred_machine_does_not_reassign_historical_records(self):
        """Neither old nor new client grant proves ownership after a transfer."""
        actor_b = get_user_model().objects.create_user(username='attribution-reader-b')
        actor_b.groups.add(*self.actor.groups.all())
        client_b = Client.objects.create(name='Synthetic B', code='proof-b')
        ClientScopeGrant.objects.create(user=actor_b, client=client_b)
        work_attachment = self.attachment('workorder', self.work.pk)
        machine_attachment = self.attachment('assetmachine', self.machine.pk)
        self.machine.client = client_b
        self.machine.save(update_fields=['client'])
        self.assertTrue(scoped_work_orders(actor_b).filter(pk=self.work.pk).exists())
        self.assertIsNotNone(
            read_machine(actor_b, environment='synthetic', machine_pk=self.machine.pk)
        )
        for actor in (self.actor, actor_b):
            with self.subTest(actor=actor.pk):
                self.assertIsNone(
                    read_work_order(
                        actor, environment='synthetic', work_pk=self.work.pk
                    )
                )
                self.assertIsNone(
                    read_maintenance_record(
                        actor, environment='synthetic', record_pk=self.maintenance.pk
                    )
                )
                self.assertIsNone(
                    read_maintenance_work_link(
                        actor, environment='synthetic', record_pk=self.maintenance.pk
                    )
                )
                self.assertIsNone(
                    read_work_attachment(
                        actor, environment='synthetic', attachment_pk=work_attachment.pk
                    )
                )
                self.assertIsNone(
                    read_machine_attachment(
                        actor,
                        environment='synthetic',
                        attachment_pk=machine_attachment.pk,
                    )
                )
