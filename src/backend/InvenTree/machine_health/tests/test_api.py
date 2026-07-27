"""API tests for the machine health surfaces and signed webhook ingestion."""

import json
import time
import uuid
from datetime import timedelta

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from machine_health.connectors.webhook import expected_signature
from machine_health.models import (
    AnomalySeverity,
    AnomalyStatus,
    HealthEvidenceSnapshot,
    HealthState,
    MachineAnomaly,
    MachineSignalState,
)
from machine_health.services.anomalies import fingerprint_for, record_anomaly

from assets.models import AssetMachine
from InvenTree.unit_test import InvenTreeAPITestCase

from .fixtures import HealthEnvMixin

WEBHOOK_SECRET = 'test-shared-secret'


class MachineHealthReadApiTest(HealthEnvMixin, InvenTreeAPITestCase):
    """Machine-scoped reads: summary, signals, anomalies and snapshots."""

    roles = ['work_order.view', 'work_order.change']

    def setUp(self):
        """Build a machine with one bounded signal and a current reading."""
        super().setUp()
        self.build_health_env()
        self.now = timezone.now()
        self.set_signal(3.0, observed_at=self.now)

    def url(self, suffix=''):
        """Return a machine-nested health URL."""
        return f'/api/machine-health/machines/{self.machine.pk}/health/{suffix}'

    def test_summary_reports_condition_and_source_status(self):
        """The summary carries state, freshness and connection health."""
        response = self.get(self.url(), expected_code=200)

        self.assertEqual(response.data['state'], HealthState.NORMAL)
        self.assertTrue(response.data['configured'])
        self.assertEqual(response.data['signal_count'], 1)
        self.assertEqual(len(response.data['sources']), 1)

    def test_unconfigured_machine_returns_an_explicit_empty_state(self):
        """A machine with no source is unknown, not an error."""
        bare = AssetMachine.objects.create(name=f'Bare {uuid.uuid4().hex[:6]}')

        response = self.get(
            f'/api/machine-health/machines/{bare.pk}/health/', expected_code=200
        )

        self.assertEqual(response.data['state'], HealthState.UNKNOWN)
        self.assertFalse(response.data['configured'])

    def test_signals_expose_value_freshness_and_limits(self):
        """Each row carries enough to render a value with its provenance."""
        response = self.get(self.url('signals/'), expected_code=200)

        [row] = response.data['results']
        self.assertEqual(row['display_name'], self.binding.display_name)
        self.assertEqual(row['value'], 3.0)
        self.assertEqual(row['unit'], 'mm/s')
        self.assertFalse(row['stale'])
        self.assertEqual(row['limits']['critical_max'], 9.0)

    def test_anomaly_list_defaults_to_the_active_set(self):
        """The blade shows what still demands attention."""
        record_anomaly(
            machine=self.machine,
            fingerprint=fingerprint_for('api', 'open'),
            title='Open condition',
            severity=AnomalySeverity.WARNING,
        )
        resolved, _ = record_anomaly(
            machine=self.machine,
            fingerprint=fingerprint_for('api', 'resolved'),
            title='Old condition',
            severity=AnomalySeverity.INFO,
        )
        resolved.status = AnomalyStatus.RESOLVED
        resolved.save(update_fields=['status'])

        active = self.get(self.url('anomalies/'), expected_code=200)
        self.assertEqual(active.data['count'], 1)

        every = self.get(self.url('anomalies/?status=all'), expected_code=200)
        self.assertEqual(every.data['count'], 2)

    def test_anomaly_list_rejects_an_unknown_status(self):
        """Filters come from a closed vocabulary."""
        response = self.get(self.url('anomalies/?status=nonsense'), expected_code=400)
        self.assertEqual(response.data['code'], 'INVALID_STATUS')

    def test_another_machines_anomaly_is_not_reachable(self):
        """Scope is applied before lookup; ids from elsewhere resolve to 404."""
        other_machine = AssetMachine.objects.create(
            name=f'Other {uuid.uuid4().hex[:6]}'
        )
        foreign, _ = record_anomaly(
            machine=other_machine,
            fingerprint=fingerprint_for('api', 'foreign'),
            title='Someone else',
            severity=AnomalySeverity.WARNING,
        )

        self.post(
            self.url(f'anomalies/{foreign.pk}/acknowledge/'), {}, expected_code=404
        )

    def test_acknowledge_records_the_actor(self):
        """Acknowledging attributes the action and does not resolve it."""
        anomaly, _ = record_anomaly(
            machine=self.machine,
            fingerprint=fingerprint_for('api', 'ack'),
            title='Bearing temperature rising',
            severity=AnomalySeverity.WARNING,
        )

        response = self.post(
            self.url(f'anomalies/{anomaly.pk}/acknowledge/'),
            {'note': 'Route tech dispatched'},
            expected_code=200,
        )

        self.assertEqual(response.data['status'], AnomalyStatus.ACKNOWLEDGED)
        self.assertIsNotNone(response.data['acknowledged_at'])
        self.assertIsNone(response.data['resolved_at'])

    def test_evidence_capture_returns_citable_snapshots(self):
        """Capturing evidence returns immutable snapshot ids for citation."""
        anomaly, _ = record_anomaly(
            machine=self.machine,
            fingerprint=fingerprint_for('api', 'evidence'),
            title='Vibration alarm',
            severity=AnomalySeverity.CRITICAL,
            bindings=[self.binding],
        )

        response = self.post(
            self.url(f'anomalies/{anomaly.pk}/evidence/'), {}, expected_code=201
        )

        self.assertEqual(response.data['count'], 1)
        [snapshot] = response.data['results']
        self.assertEqual(snapshot['signal_label'], self.binding.display_name)
        self.assertTrue(HealthEvidenceSnapshot.objects.filter(id=snapshot['id']).exists())


class HealthReadPermissionTest(HealthEnvMixin, InvenTreeAPITestCase):
    """Reading condition and changing it are different authorities."""

    roles = ['work_order.view']

    def setUp(self):
        """Build a machine with one open anomaly."""
        super().setUp()
        self.build_health_env()
        self.anomaly, _ = record_anomaly(
            machine=self.machine,
            fingerprint=fingerprint_for('perm', 'ack'),
            title='Condition',
            severity=AnomalySeverity.WARNING,
        )

    def test_view_only_actor_can_read_but_not_acknowledge(self):
        """View access does not authorize changing anomaly state."""
        self.get(
            f'/api/machine-health/machines/{self.machine.pk}/health/', expected_code=200
        )
        self.post(
            f'/api/machine-health/machines/{self.machine.pk}/health/anomalies/'
            f'{self.anomaly.pk}/acknowledge/',
            {},
            expected_code=403,
        )
        self.anomaly.refresh_from_db()
        self.assertEqual(self.anomaly.status, AnomalyStatus.OPEN)


@override_settings(
    AIMMS_HEALTH_WEBHOOK_SECRETS={'health/webhook-test': WEBHOOK_SECRET}
)
class WebhookIngestApiTest(HealthEnvMixin, InvenTreeAPITestCase):
    """Signed webhook ingestion: signature, replay window and delivery dedupe."""

    roles = ['work_order.view']

    def setUp(self):
        """Build a machine and point its source at the test secret."""
        super().setUp()
        self.build_health_env()
        self.source.secret_ref = 'health/webhook-test'
        self.source.save(update_fields=['secret_ref'])
        self.url = f'/api/machine-health/ingest/{self.source.pk}/'
        cache.clear()
        self.addCleanup(cache.clear)

    def deliver(self, payload, *, secret=WEBHOOK_SECRET, timestamp=None, delivery=None):
        """Post a signed delivery to the ingest endpoint."""
        body = json.dumps(payload).encode()
        timestamp = timestamp if timestamp is not None else str(int(time.time()))
        delivery = delivery or uuid.uuid4().hex
        return self.client.post(
            self.url,
            data=body,
            content_type='application/json',
            HTTP_X_AIMMS_SIGNATURE=expected_signature(secret, timestamp, body),
            HTTP_X_AIMMS_TIMESTAMP=timestamp,
            HTTP_X_AIMMS_DELIVERY=delivery,
        )

    def readings_payload(self, value=3.0):
        """Return a single-reading delivery body."""
        return {
            'readings': [
                {
                    'external_key': self.binding.external_key,
                    'value': value,
                    'observed_at': timezone.now().isoformat(),
                    'quality': 'good',
                }
            ]
        }

    def test_signed_delivery_is_accepted(self):
        """A correctly signed batch lands as current signal state."""
        response = self.deliver(self.readings_payload())

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['accepted'], 1)
        self.assertEqual(
            MachineSignalState.objects.get(binding=self.binding).value['value'], 3.0
        )

    def test_wrong_signature_is_refused(self):
        """A body signed with the wrong secret never reaches ingestion."""
        response = self.deliver(self.readings_payload(), secret='not-the-secret')

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'SIGNATURE_INVALID')
        self.assertFalse(MachineSignalState.objects.exists())

    def test_missing_signature_is_refused(self):
        """An unsigned delivery is rejected outright."""
        response = self.client.post(
            self.url, data=b'{}', content_type='application/json'
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'SIGNATURE_MISSING')

    def test_stale_timestamp_is_refused(self):
        """A captured request cannot be replayed later."""
        old = str(int(time.time()) - 3600)
        response = self.deliver(self.readings_payload(), timestamp=old)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'TIMESTAMP_OUT_OF_WINDOW')

    def test_repeated_delivery_id_is_refused(self):
        """A delivery cannot be replayed inside the window either."""
        delivery = uuid.uuid4().hex
        first = self.deliver(self.readings_payload(), delivery=delivery)
        second = self.deliver(self.readings_payload(value=9.9), delivery=delivery)

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 401)
        self.assertEqual(second.json()['code'], 'REPLAYED_DELIVERY')
        self.assertEqual(
            MachineSignalState.objects.get(binding=self.binding).value['value'], 3.0
        )

    def test_source_without_a_configured_secret_fails_closed(self):
        """Unsigned data is never accepted because a secret is missing."""
        self.source.secret_ref = 'health/not-configured'
        self.source.save(update_fields=['secret_ref'])

        response = self.deliver(self.readings_payload())

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'SECRET_UNRESOLVED')

    def test_threshold_rules_run_after_the_batch_lands(self):
        """One evaluation sees the whole update and raises the anomaly."""
        response = self.deliver(self.readings_payload(value=12.0))

        self.assertEqual(response.status_code, 202)
        anomaly = MachineAnomaly.objects.get(machine=self.machine)
        self.assertEqual(anomaly.severity, AnomalySeverity.CRITICAL)
        self.assertEqual(anomaly.detector, 'threshold')

    def test_source_alarm_is_recorded_against_the_mapped_machine(self):
        """A declared alarm becomes an anomaly on the tag's machine."""
        payload = {
            'alarms': [
                {
                    'external_key': self.binding.external_key,
                    'alarm_code': 'VIB-HI',
                    'title': 'High vibration',
                    'severity': 'critical',
                    'message': 'Alarm raised at the station HMI',
                    'observed_at': (
                        timezone.now() - timedelta(minutes=1)
                    ).isoformat(),
                }
            ]
        }
        response = self.deliver(payload)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['alarms_recorded'], 1)
        anomaly = MachineAnomaly.objects.get(machine=self.machine)
        self.assertEqual(anomaly.detector, 'source_alarm')
        self.assertEqual(anomaly.alarm_code, 'VIB-HI')

    def test_alarm_for_an_unmapped_tag_is_dropped(self):
        """A source may not invent machines by naming unknown tags."""
        payload = {
            'alarms': [
                {
                    'external_key': 'NOT-MAPPED',
                    'alarm_code': 'X',
                    'title': 'Ghost alarm',
                    'severity': 'critical',
                }
            ]
        }
        response = self.deliver(payload)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['alarms_recorded'], 0)
        self.assertFalse(MachineAnomaly.objects.exists())
