"""An exact retained citation is not limited to the recent-snapshot list."""

from uuid import uuid4

from django.utils import timezone

from assets.health_models import SnapshotReason
from assets.models import AssetMachine, Client
from InvenTree.unit_test import InvenTreeAPITestCase
from machine_health.services.snapshots import capture_current_signal

from .fixtures import HealthEnvMixin


class RetainedEvidenceReadTests(HealthEnvMixin, InvenTreeAPITestCase):
    """Read one frozen snapshot through its authorized machine only."""

    roles = ['work_order.view']

    def setUp(self):
        """Freeze a fault reading, then restore the current reading."""
        super().setUp()
        self.build_health_env()
        self.set_signal(12.0)
        self.snapshot = capture_current_signal(
            self.binding, reason=SnapshotReason.ANOMALY_REPAIR, now=timezone.now()
        )
        self.set_signal(3.0)

    def url(self, machine=None, snapshot=None):
        """A machine-scoped exact citation, never a global snapshot URL."""
        return (
            f'/api/machine-health/machines/{machine or self.machine.pk}/health/'
            f'snapshots/?snapshot={snapshot or self.snapshot.pk}'
        )

    def test_exact_citation_keeps_original_fault_value(self):
        """Normal current observations do not rewrite retained evidence."""
        response = self.get(self.url(), expected_code=200)
        self.assertEqual(response.data['count'], 1)
        [snapshot] = response.data['results']
        self.assertEqual(snapshot['statistics']['latest'], 12.0)
        self.assertEqual(snapshot['id'], str(self.snapshot.pk))
        self.assertEqual(snapshot['content_hash'], self.snapshot.content_hash)

    def test_citation_names_the_human_and_system_recorder_and_reason(self):
        """Retained provenance survives opening the exact Health citation."""
        captured = capture_current_signal(
            self.binding, reason=SnapshotReason.MANUAL_REPAIR,
            actor=self.user, system_actor='review-fixture', now=timezone.now()
        )
        response = self.get(self.url(snapshot=captured.pk), expected_code=200)
        [snapshot] = response.data['results']
        self.assertEqual(snapshot['captured_by_name'], self.user.get_full_name() or self.user.get_username())
        self.assertEqual(snapshot['system_actor'], 'review-fixture')
        self.assertEqual(snapshot['reason'], SnapshotReason.MANUAL_REPAIR)

    def test_missing_recorder_is_null(self):
        """Legacy evidence without a recorder must not invent one."""
        response = self.get(self.url(), expected_code=200)
        self.assertIsNone(response.data['results'][0]['captured_by_name'])

    def test_old_citation_is_not_lost_behind_the_fifty_recent_limit(self):
        """A historical link still resolves after more than fifty captures."""
        for _index in range(51):
            capture_current_signal(
                self.binding, reason=SnapshotReason.MANUAL_REPAIR, now=timezone.now()
            )
        recent = self.get(self.url().split('?')[0], expected_code=200)
        self.assertEqual(recent.data['count'], 50)
        self.assertNotIn(
            str(self.snapshot.pk), [row['id'] for row in recent.data['results']]
        )
        response = self.get(self.url(), expected_code=200)
        self.assertEqual(response.data['results'][0]['id'], str(self.snapshot.pk))

    def test_another_machine_cannot_be_used_to_read_the_snapshot(self):
        """Even another authorized machine cannot borrow this one's evidence."""
        other = AssetMachine.objects.create(name='Other pump', client=self.client_tenant)
        self.get(self.url(machine=other.pk), expected_code=404)

    def test_denied_machine_stays_denied_for_an_exact_snapshot(self):
        """An exact id does not replace the canonical client scope check."""
        other_client = Client.objects.create(name='Outside', code='outside-retained')
        self.machine.client = other_client
        self.machine.save(update_fields=['client'])
        self.get(self.url(), expected_code=404)

    def test_missing_snapshot_does_not_fall_back_to_current_or_recent(self):
        """Missing and malformed ids both fail closed."""
        for snapshot in (str(uuid4()), 'not-a-uuid'):
            self.get(self.url(snapshot=snapshot), expected_code=404)
