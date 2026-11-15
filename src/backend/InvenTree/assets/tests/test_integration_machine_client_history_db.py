"""Characterize authorization after a synthetic machine-client transfer.

This is a policy characterization, not a decision that prior history is
permitted to move with the machine or evidence of a deployed condition.
"""

import io
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from tasks.integration_attachments import read_work_attachment_content
from tasks.integration_sources import read_work_order
from tasks.models import WorkOrder
from tasks.scope import scoped_work_orders

from assets.integration_sources import read_maintenance_record
from assets.models import AssetMachine, AssetMaintenanceRecord, Client, ClientScopeGrant
from assets.tests.integration_attribution_test_support import (
    assume_synthetic_attribution,
)
from common.models import Attachment
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='unavailable-in-test',
)
@assume_synthetic_attribution
class MachineClientHistoryCharacterization(TestCase):
    """Characterize the prototype only with attribution deliberately simulated."""

    def test_pretransfer_rows_follow_current_machine_client(self):
        """Observe the current reader without choosing a historic access policy."""
        users = get_user_model()
        actor_a = users.objects.create_user(username='history-a')
        actor_b = users.objects.create_user(username='history-b')
        group = Group.objects.create(name='History readers')
        actor_a.groups.add(group)
        actor_b.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        client_a = Client.objects.create(name='Synthetic A', code='history-a')
        client_b = Client.objects.create(name='Synthetic B', code='history-b')
        ClientScopeGrant.objects.create(user=actor_a, client=client_a)
        ClientScopeGrant.objects.create(user=actor_b, client=client_b)
        machine = AssetMachine.objects.create(
            name='Synthetic transfer', client=client_a
        )
        work = WorkOrder.objects.create(
            title='Synthetic before transfer',
            machine=machine,
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
        )
        maintenance = AssetMaintenanceRecord.objects.create(
            machine=machine, date=date(2020, 1, 2), summary='Synthetic before transfer'
        )
        attachment = Attachment(
            model_type='workorder',
            model_id=work.pk,
            link='https://example.invalid/test',
        )
        attachment.save(rebuild=False)
        Attachment.objects.filter(pk=attachment.pk).update(attachment='synthetic.bin')

        def projection(actor):
            with patch('django.core.files.storage.default_storage.open') as opened:
                opened.return_value = io.BytesIO(b'synthetic-content')
                return (
                    read_work_order(actor, environment='test', work_pk=work.pk),
                    read_maintenance_record(
                        actor, environment='test', record_pk=maintenance.pk
                    ),
                    read_work_attachment_content(
                        actor,
                        environment='test',
                        attachment_pk=attachment.pk,
                        max_bytes=64,
                    ),
                )

        self.assertTrue(all(value is not None for value in projection(actor_a)))
        self.assertEqual(projection(actor_b), (None, None, None))
        machine.client = client_b
        machine.save(update_fields=['client'])
        self.assertTrue(scoped_work_orders(actor_b).filter(pk=work.pk).exists())
        self.assertEqual(projection(actor_a), (None, None, None))
        after = projection(actor_b)
        self.assertTrue(all(value is not None for value in after))
        self.assertEqual(after[2].content, b'synthetic-content')
