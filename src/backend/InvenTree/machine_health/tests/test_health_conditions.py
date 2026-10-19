"""One condition rule for a page of machines and for each machine's Health tab."""

import uuid
from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from assets.health_models import (
    HealthSource,
    MachineAnomaly,
    MachineSignalBinding,
    MachineSignalState,
    SignalQuality,
    SourceType,
)
from assets.models import AssetMachine, Client
from machine_health.services.summary import health_conditions, health_summary


class HealthConditionsTests(TestCase):
    """Every summary state, judged in one batch and one machine at a time."""

    def setUp(self):
        """Machines covering each state, including a pump on a station clock."""
        self.now = timezone.now()
        tenant = Client.objects.create(name='Conditions', code='conditions')
        self.source = HealthSource.objects.create(
            name='SCADA conditions',
            source_type=SourceType.SCADA,
            connector_type='webhook',
            secret_ref='health/conditions',
            freshness_threshold_seconds=900,
        )
        self.tenant = tenant
        self.machines = []

        def machine(name, *readings, anomalies=(), parent=None, kind='equipment'):
            # Registered stations and pumps carry their source identity.
            row = AssetMachine.objects.create(
                name=name,
                client=tenant,
                parent=parent,
                asset_type=kind,
                source_namespace='scada' if kind == 'pumphouse' else '',
                source_entity_uuid=uuid.uuid4(),
                source_key=name.lower().replace(' ', '-') if kind == 'pump' else '',
            )
            for index, (value, age, quality) in enumerate(readings):
                binding = MachineSignalBinding.objects.create(
                    machine=row,
                    source=self.source,
                    external_key=f'{name}.{index}',
                    display_name=f'{name} signal {index}',
                    signal_kind='vibration',
                    unit='mm/s',
                    normal_max=4.5,
                    warn_max=6.0,
                    critical_max=9.0,
                )
                observed = self.now - timedelta(seconds=age)
                MachineSignalState.objects.create(
                    binding=binding,
                    value={'value': value, 'unit': 'mm/s'},
                    observed_at=observed,
                    received_at=observed,
                    quality=quality,
                )
            for index, (severity, status) in enumerate(anomalies):
                MachineAnomaly.objects.create(
                    machine=row,
                    fingerprint=f'{name}-{index}',
                    title='synthetic',
                    severity=severity,
                    status=status,
                    detector='threshold',
                    first_observed_at=self.now,
                    last_observed_at=self.now,
                )
            self.machines.append(row)
            return row

        good = SignalQuality.GOOD
        machine('Normal', (3.0, 30, good))
        machine('Warning', (5.0, 30, good), (3.0, 30, good))
        machine(
            'Critical by anomaly', (3.0, 30, good), anomalies=[('critical', 'open')]
        )
        machine('Offline', (3.0, 4000, good))
        machine('Partly stale', (3.0, 30, good), (8.0, 4000, good))
        machine('Poor quality', (3.0, 30, good), (9.5, 30, SignalQuality.BAD))
        machine('Unmapped with anomaly', anomalies=[('warning', 'acknowledged')])
        machine('Unmapped', anomalies=[('critical', 'resolved')])
        station = machine('Station', (3.0, 400 * 86400 + 30, good), kind='pumphouse')
        # The station's recorded history ended 400 days ago; it and its pumps
        # present on the station's shifted clock, so those old readings read
        # as current (see display_time).
        self.source.config = {
            'data_ranges': {
                str(station.source_entity_uuid): {
                    'from': (self.now - timedelta(days=410)).isoformat(),
                    'to': (self.now - timedelta(days=400)).isoformat(),
                }
            }
        }
        self.source.save()
        machine(
            'Shifted pump', (5.0, 400 * 86400 + 60, good), parent=station, kind='pump'
        )

    def test_batch_matches_each_health_tab(self):
        """Every field the Health tab shows comes out the same in the batch."""
        batch = health_conditions(self.machines, now=self.now)
        for machine in self.machines:
            with self.subTest(machine=machine.name):
                single = health_summary(machine, now=self.now)
                single.pop('sources')
                self.assertEqual(batch[machine.pk], single)

    def test_each_state_is_reached(self):
        """The fixture really exercises every branch, not one state many times."""
        batch = health_conditions(self.machines, now=self.now)
        states = {
            machine.name: (batch[machine.pk]['state'], batch[machine.pk]['configured'])
            for machine in self.machines
        }
        self.assertEqual(
            states,
            {
                'Normal': ('normal', True),
                'Warning': ('warning', True),
                'Critical by anomaly': ('critical', True),
                'Offline': ('offline', True),
                'Partly stale': ('normal', True),
                'Poor quality': ('normal', True),
                'Unmapped with anomaly': ('warning', False),
                'Unmapped': ('unknown', False),
                'Station': ('normal', True),
                'Shifted pump': ('warning', True),
            },
        )
        partly = batch[self.machines[4].pk]
        self.assertEqual(
            (partly['stale_signal_count'], partly['degraded_data']), (1, True)
        )
        self.assertTrue(batch[self.machines[5].pk]['degraded_data'])

    def test_a_page_costs_the_same_queries_however_long(self):
        """Bindings, station clocks and anomalies are each read once."""
        with CaptureQueriesContext(connection) as few:
            health_conditions(self.machines[:2], now=self.now)
        with CaptureQueriesContext(connection) as all_machines:
            health_conditions(self.machines, now=self.now)
        self.assertEqual(len(all_machines), len(few) + 1)  # + station clock
        self.assertLessEqual(len(all_machines), 3)
        self.assertEqual(health_conditions([], now=self.now), {})
