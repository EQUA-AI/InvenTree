"""One command must be able to give a fresh deployment its whole estate.

`onboard_pumphouse_estate` registers the stations, imports each one's
dictionary from the tag file the manifest names, and applies its review - so a
deployment that has only the code and the telemetry gets the layer between
them in a single pass. This test is the proof that the committed artefacts
still do that, because every one of them can drift independently: a re-exported
pack can reintroduce the hash that refuses to replay, a tag file can fall
behind the paths a pack names, and the manifest can point at either.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.utils import timezone
from django.test import TestCase

from assets.health_models import HealthSource, MachineSignalBinding
from assets.ingestion_models import IngestionCheckpoint
from assets.models import AssetMachine, Client
from assets.registry_models import DictionaryPoint
from machine_health.connectors.cosmos_pumphouse import to_epoch_ms

REVIEW_DIR = Path(__file__).resolve().parents[5] / 'contrib/cosmos/review'
MANIFEST = REVIEW_DIR / 'estate.manifest.json'

#: What each station must end up with: approved points, and its bay count.
EXPECTED = {
    'PH_3': {'approved': 802, 'withheld': 103, 'pumps': 14},
    'PH_2': {'approved': 452, 'withheld': 467, 'pumps': 12},
    'PH_7': {'approved': 198, 'withheld': 173, 'pumps': 4},
}

#: What `discover_data_range` records for this estate. Activation reads it to
#: decide where a cursor starts, so the runbook must record it *before*
#: activating - a cursor placed at the wall clock can never come back.
RECORDED_END = '2025-07-11T23:59:59.999000+00:00'


class EstateBootstrapTests(TestCase):
    """The production bootstrap, run against a database that has never seen it."""

    def setUp(self):
        """Everything section 14 says must exist before onboarding."""
        call_command('load_pump_catalogue', stdout=StringIO())
        self.source = HealthSource.objects.create(
            name='Cosmos probe',
            source_type='iot',
            connector_type='cosmos_pumphouse',
            client=Client.objects.create(name='Estate', code='estate'),
            active=True,
            config={
                'endpoint': 'https://example.documents.azure.com:443/',
                'database': 'aimms',
                'readings_container': 'pumphouse_readings',
                'data_ranges': {
                    station['source_uuid']: {
                        'from': '2025-07-02T01:01:11.976000+00:00',
                        'to': RECORDED_END,
                    }
                    for station in json.loads(MANIFEST.read_text())['stations']
                },
            },
        )

    def onboard(self, **options):
        """Run the command the runbook runs."""
        out = StringIO()
        call_command(
            'onboard_pumphouse_estate',
            MANIFEST,
            source=self.source.pk,
            stdout=out,
            **options,
        )
        return json.loads(out.getvalue())

    def test_one_command_registers_reviews_and_binds_the_whole_estate(self):
        """The bootstrap, end to end, from nothing.

        `--activate` is what turns an approved point into a drawable signal:
        it is `activate_station` that writes the bindings and opens the
        ingestion checkpoint, so onboarding without it leaves a reviewed
        dictionary and nothing to read it with.
        """
        report = self.onboard(activate=True)

        self.assertEqual(len(report['stations']), 3)
        for record in report['stations']:
            station = AssetMachine.objects.get(pk=record['station'])
            expected = EXPECTED[station.source_key]
            with self.subTest(station=station.source_key):
                self.assertEqual(len(record['pumps']), expected['pumps'])
                self.assertEqual(record['approved'], expected['approved'])
                self.assertEqual(
                    DictionaryPoint.objects
                    .filter(station=station)
                    .exclude(status='approved')
                    .exclude(review_note='')
                    .count(),
                    expected['withheld'],
                    'every unapproved point should carry its recorded reason',
                )
                self.assertEqual(
                    MachineSignalBinding.objects
                    .filter(source=self.source)
                    .filter(machine__in=[station, *station.children.all()])
                    .count(),
                    expected['approved'],
                    'activation turns every approved point into a signal',
                )

    def test_activation_enters_the_recorded_window_not_the_wall_clock(self):
        """The cursor only moves forward, so its opening position is final.

        Placed five minutes before *now*, a recorded window's cursor sits 442
        days past anything the connector will return, and the station polls
        successfully for ever while ingesting nothing.
        """
        self.onboard(activate=True)

        expected = to_epoch_ms(timezone.datetime.fromisoformat(RECORDED_END)) - 300_001
        checkpoints = IngestionCheckpoint.objects.filter(
            source=self.source, active=True
        )

        self.assertEqual(checkpoints.count(), 3, 'one checkpoint per station')
        # All three stations share one recorded end, so one cursor value is
        # the right answer here, not three.
        self.assertEqual(
            set(checkpoints.values_list('sub_time_period', flat=True)), {expected}
        )
        self.assertLess(
            expected,
            to_epoch_ms(timezone.now()),
            'the cursor must sit in the recorded past, not at the wall clock',
        )

    def test_a_dry_run_writes_nothing(self):
        """The runbook tells an operator to preview first; it must mean it."""
        report = self.onboard(dry_run=True)

        self.assertTrue(report['dry_run'])
        self.assertEqual(AssetMachine.objects.filter(asset_type='pumphouse').count(), 0)
        self.assertEqual(DictionaryPoint.objects.count(), 0)

    def test_onboarding_twice_changes_nothing_the_second_time(self):
        """An interrupted rollout is resumed by running it again."""
        first = self.onboard(activate=True)
        second = self.onboard(activate=True)

        self.assertEqual(
            [r['station'] for r in first['stations']],
            [r['station'] for r in second['stations']],
            'the same rows, not duplicates',
        )
        self.assertEqual(AssetMachine.objects.filter(asset_type='pumphouse').count(), 3)
        self.assertEqual(
            MachineSignalBinding.objects.count(),
            sum(e['approved'] for e in EXPECTED.values()),
        )
