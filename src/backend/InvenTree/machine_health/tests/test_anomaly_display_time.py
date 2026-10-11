"""An anomaly card must be dated in the same clock as the reading it cites.

Every current-condition surface on the blade presents a station whose history is
a recorded window in a shifted clock, so the signal row that produced an anomaly
says "5 s ago". The anomaly serializer did not shift anything, and until this
change no anomaly had ever existed to expose it: the poll path never evaluated
thresholds, so the only writer was a webhook that cannot serve a Cosmos source.

The first anomaly the estate ever raises would have been dated 442 days before
the reading beside it, on the same screen.
"""

from datetime import timedelta
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from assets.health_models import (
    AnomalySeverity,
    AnomalyStatus,
    HealthSource,
    MachineAnomaly,
    MachineSignalBinding,
    SourceType,
)
from assets.models import AssetMachine, Client
from machine_health.serializers import MachineAnomalySerializer


class AnomalyDisplayTimeTest(TestCase):
    """Observation times are shifted; the times a person acted are not."""

    def setUp(self):
        """A pump under a station whose recorded window ended 400 days ago."""
        suffix = uuid4().hex[:6]
        self.tenant = Client.objects.create(name=f'Shift {suffix}', code=f'sh-{suffix}')
        self.station = AssetMachine.objects.create(
            name='Recorded station',
            asset_type='pumphouse',
            client=self.tenant,
            source_namespace='shift',
            source_entity_uuid=uuid4(),
        )
        self.pump = AssetMachine.objects.create(
            name='Pump 01',
            asset_type='pump',
            client=self.tenant,
            parent=self.station,
            source_key='P1',
        )
        self.now = timezone.now().replace(microsecond=0)
        self.recorded_end = self.now - timedelta(days=400)
        self.source = HealthSource.objects.create(
            name=f'Recorded source {suffix}',
            client=self.tenant,
            source_type=SourceType.HISTORIAN,
            connector_type='cosmos_pumphouse',
            config={
                'data_ranges': {
                    str(self.station.source_entity_uuid): {
                        'from': (self.recorded_end - timedelta(days=10)).isoformat(),
                        'to': self.recorded_end.isoformat(),
                    }
                }
            },
        )
        self.binding = MachineSignalBinding.objects.create(
            machine=self.pump,
            source=self.source,
            external_key='/dex/PUMP1_STATOR_WINDING_TEMP1',
            display_name='Stator winding 1',
            unit='degC',
            warn_max=125.0,
            critical_max=145.0,
        )

    def anomaly(self, *, observed_at):
        """One threshold anomaly observed at a plant instant."""
        return MachineAnomaly.objects.create(
            machine=self.pump,
            source=self.source,
            fingerprint=uuid4().hex,
            title='Stator winding 1 outside configured limits',
            severity=AnomalySeverity.CRITICAL,
            status=AnomalyStatus.OPEN,
            detector='threshold',
            first_observed_at=observed_at,
            last_observed_at=observed_at,
        )

    def test_observation_times_are_moved_into_the_display_clock(self):
        """The card lands next to the reading, not 400 days behind it."""
        observed = self.recorded_end - timedelta(minutes=5)
        data = MachineAnomalySerializer(self.anomaly(observed_at=observed)).data

        shift = int((self.now - self.recorded_end).total_seconds())
        self.assertTrue(data['display_shifted'])
        # The clock moves on between setUp and here, so allow a second of drift.
        self.assertAlmostEqual(data['display_shift_seconds'], shift, delta=5)
        self.assertGreater(
            timezone.datetime.fromisoformat(data['first_observed_at']),
            self.now - timedelta(minutes=10),
        )

    def test_the_stored_instant_stays_on_the_plant_clock(self):
        """Only the presentation moves: the row is still checkable against source."""
        observed = self.recorded_end - timedelta(minutes=5)
        anomaly = self.anomaly(observed_at=observed)
        MachineAnomalySerializer(anomaly).data

        anomaly.refresh_from_db()
        self.assertEqual(anomaly.first_observed_at, observed)
        self.assertEqual(anomaly.last_observed_at, observed)

    def test_acknowledgement_and_resolution_times_are_not_shifted(self):
        """A person acted at a real moment; moving it would misreport when."""
        observed = self.recorded_end - timedelta(minutes=5)
        anomaly = self.anomaly(observed_at=observed)
        anomaly.resolved_at = self.now
        anomaly.save(update_fields=['resolved_at'])

        data = MachineAnomalySerializer(anomaly).data

        self.assertEqual(
            timezone.datetime.fromisoformat(data['resolved_at']).replace(microsecond=0),
            self.now,
        )

    def test_a_current_station_is_not_shifted_at_all(self):
        """A source that is already live must report no shift and move nothing."""
        self.source.config = {
            'data_ranges': {
                str(self.station.source_entity_uuid): {
                    'from': (self.now - timedelta(days=10)).isoformat(),
                    'to': self.now.isoformat(),
                }
            }
        }
        self.source.save(update_fields=['config'])
        observed = self.now - timedelta(minutes=5)

        data = MachineAnomalySerializer(self.anomaly(observed_at=observed)).data

        self.assertFalse(data['display_shifted'])
        self.assertEqual(data['display_shift_seconds'], 0)
        self.assertEqual(
            timezone.datetime.fromisoformat(data['first_observed_at']), observed
        )
