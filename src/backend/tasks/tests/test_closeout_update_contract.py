"""P25: native HTTP replay confirms the original internal update exactly once."""

from django.test import TestCase, override_settings

from rest_framework.test import APIClient

from assets.models import AssetMaintenanceRecord
from tasks.models import WorkOrderCloseout, WorkOrderEvent
from tasks.tests.closeout_fixtures import CLOSEOUT_FLAGS, VALID_CLOSEOUT, CloseoutEnvMixin


@override_settings(**CLOSEOUT_FLAGS)
class CloseoutUpdateContractTests(CloseoutEnvMixin, TestCase):
    """No reconciliation route is needed: replay the original supported command."""

    def setUp(self):
        """One authorized standalone work order and one immutable intent."""
        self.build_env(username='closeout-update-contract')
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.url = f'/api/tasks/work-orders/{self.work_order.pk}/complete/'
        self.payload = {
            **VALID_CLOSEOUT,
            'expected_version': self.work_order.lifecycle_version,
            'idempotency_key': 'original-update-operation',
        }

    def test_exact_replay_after_commit_returns_original_receipt_without_duplicate(self):
        """A lost response is recovered using the old version and original key."""
        first = self.client.post(self.url, self.payload, format='json')
        self.assertEqual(first.status_code, 200)
        event = WorkOrderEvent.objects.get(pk=first.data['event_id'])
        original_time = event.created_at
        self.work_order.refresh_from_db()
        self.assertGreater(self.work_order.lifecycle_version, self.payload['expected_version'])
        replay = self.client.post(self.url, self.payload, format='json')
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data, first.data)
        self.assertEqual(WorkOrderCloseout.objects.filter(work_order=self.work_order).count(), 1)
        self.assertEqual(AssetMaintenanceRecord.objects.filter(work_order=self.work_order).count(), 1)
        self.assertEqual(WorkOrderEvent.objects.filter(work_order=self.work_order, event_type='COMPLETED').count(), 1)
        event.refresh_from_db()
        events = self.client.get(f'/api/tasks/work-orders/{self.work_order.pk}/events/').data['results']
        original = next(row for row in events if row['id'] == event.pk)
        self.assertEqual(original['idempotency_key'], self.payload['idempotency_key'])
        from tasks.workorder_serializers import WorkOrderEventSerializer

        self.assertEqual(event.created_at, original_time)
        self.assertEqual(original['created_at'], WorkOrderEventSerializer(event).data['created_at'])

    def test_changed_payload_or_version_cannot_reuse_the_original_key(self):
        """The frontend must preserve the original payload when reconciling."""
        self.assertEqual(self.client.post(self.url, self.payload, format='json').status_code, 200)
        for change in ({'result': 'Different result'}, {'expected_version': self.payload['expected_version'] + 1}):
            response = self.client.post(self.url, {**self.payload, **change}, format='json')
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.data['code'], 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(WorkOrderCloseout.objects.filter(work_order=self.work_order).count(), 1)
