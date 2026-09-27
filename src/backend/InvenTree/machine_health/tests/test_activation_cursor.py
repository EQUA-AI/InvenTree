"""Where a newly activated station starts reading.

The cursor only moves forward, so its opening position is the one decision
that cannot be corrected later. Placed after the newest document a source will
ever return, a station polls successfully and for ever without ingesting
anything - and reports no error, because finding no documents is not one.
"""

from __future__ import annotations

from datetime import timedelta

from django.test import SimpleTestCase
from django.utils import timezone

from assets.activation import _initial_cursor
from assets.health_models import HealthSource
from assets.models import AssetMachine
from machine_health.connectors.cosmos_pumphouse import (
    CosmosPumphouseConnector,
    to_epoch_ms,
)

STATION_UUID = 'bafc976f-1ccc-4a91-aaa6-c3eac2470d36'
RECORDED_END = '2025-07-11T23:59:59.999000+00:00'


def source_for(data_ranges=None):
    """An unsaved source carrying only what the cursor rule reads."""
    config = {
        'endpoint': 'https://example.documents.azure.com:443/',
        'database': 'aimms',
        'readings_container': 'pumphouse_readings',
    }
    if data_ranges is not None:
        config['data_ranges'] = data_ranges
    return HealthSource(
        name='probe', source_type='iot', connector_type='cosmos_pumphouse',
        active=True, config=config,
    )


class InitialCursorTests(SimpleTestCase):
    """A live source and a recorded window are entered differently."""

    def setUp(self):
        """One station, and the instant the estate's window actually ends."""
        self.station = AssetMachine(
            name='Cedar Creek', asset_type='pumphouse', source_entity_uuid=STATION_UUID
        )
        self.now = timezone.now()
        self.recorded_end = timezone.datetime.fromisoformat(RECORDED_END)

    def recorded(self):
        """A source whose station has the estate's recorded window."""
        return source_for({STATION_UUID: {'from': '2025-07-02T01:01:11.976000+00:00', 'to': RECORDED_END}})

    def test_a_live_source_is_entered_at_its_validity_window(self):
        """Nothing changes for a source that is genuinely live."""
        cursor = _initial_cursor(self.station, source_for(), self.now)

        self.assertEqual(cursor, to_epoch_ms(self.now) - 300_000 - 1)

    def test_a_recorded_window_is_entered_before_its_own_end(self):
        """The five minutes of validity, on the clock the data has."""
        cursor = _initial_cursor(self.station, self.recorded(), self.now)

        self.assertEqual(cursor, to_epoch_ms(self.recorded_end) - 300_000 - 1)

    def test_the_cursor_lands_below_the_horizon_the_connector_will_read_to(self):
        """The bug this rule exists to prevent, stated as the two agreeing."""
        source = self.recorded()
        cursor = _initial_cursor(self.station, source, self.now)
        connector = CosmosPumphouseConnector(source)

        ceiling = connector.read_ceiling(STATION_UUID, to_epoch_ms(self.now))

        self.assertLess(
            cursor,
            ceiling,
            'a cursor above the read ceiling can never ingest, and never come back',
        )
        self.assertEqual(ceiling - cursor, 300_000 + 1)

    def test_a_window_too_recent_to_shift_is_still_treated_as_live(self):
        """The rule must agree with read_ceiling about which windows are recorded."""
        end = self.now - timedelta(minutes=5)
        source = source_for({
            STATION_UUID: {'from': (end - timedelta(days=1)).isoformat(), 'to': end.isoformat()}
        })

        cursor = _initial_cursor(self.station, source, self.now)

        self.assertEqual(cursor, to_epoch_ms(self.now) - 300_000 - 1)
        self.assertEqual(
            CosmosPumphouseConnector(source).read_ceiling(
                STATION_UUID, to_epoch_ms(self.now)
            ),
            to_epoch_ms(self.now),
            'read_ceiling does not clamp this window either',
        )
