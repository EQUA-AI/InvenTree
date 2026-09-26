"""Sampled, multi-signal history for the Performance blade.

What is worth pinning: a short window comes back whole and a long one comes
back sampled, and the response says which; every point is a reading the source
gave, never an average; a gap in the source stays a gap; and the scope of a
series is the machine and its station, not the whole estate.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from assets.health_models import HealthSource, MachineSignalBinding, SourceType
from assets.models import Client
from assets.registry import ensure_pump, register_station
from InvenTree.unit_test import InvenTreeAPITestCase
from machine_health.connectors.base import (
    COMPLETE_READ_MAX_DOCUMENTS,
    EXPECTED_SAMPLE_INTERVAL_SECONDS,
    MAX_SERIES_POINTS,
    MAX_SERIES_WINDOW_SECONDS,
    MIN_SERIES_POINTS,
    HealthConnector,
    Reading,
    register,
    slot_edges,
)
from machine_health.services.series import (
    MODE_COMPLETE,
    MODE_SAMPLED,
    SeriesError,
    read_series,
)

from .fixtures import HealthEnvMixin

CADENCE = timedelta(seconds=EXPECTED_SAMPLE_INTERVAL_SECONDS)


@register
class _SeriesHistorian(HealthConnector):
    """A source that serves history through the base class's default sampler.

    It has ``read_windows`` and nothing cleverer, which is exactly the case the
    default :meth:`HealthConnector.sample_windows` exists for.
    """

    key = 'test-series-historian'

    #: Readings the "remote platform" holds, keyed by tag.
    canned: dict[str, list[Reading]] = {}
    #: Every window the source was asked for, in the source's own clock.
    asked: list[tuple] = []
    #: An exception to raise instead of answering.
    failure: Exception | None = None

    def check(self):
        """Always reachable."""
        return True, ''

    def read_latest(self, external_keys):
        """Not used by the series path."""
        return []

    def read_windows(self, external_keys, start, end, *, max_samples=None):
        """Return canned readings inside the window, per key."""
        type(self).asked.append((start, end))
        if type(self).failure is not None:
            raise type(self).failure
        return {
            key: [
                reading
                for reading in type(self).canned.get(key, [])
                if start <= reading.observed_at < end
            ]
            for key in external_keys
        }


class _TimeoutError(Exception):
    """Named like the SDK's timeout, which is how the service classifies it."""


def moment_of(sample, like):
    """The instant a sample encodes, in the same awareness as ``like``.

    The project runs its tests with ``USE_TZ`` off, so fixture instants are
    naive; the service encodes them with ``timestamp()``, which reads a naive
    instant as local time. Decoding has to make the same choice or every
    comparison below would be naive against aware.
    """
    return datetime.fromtimestamp(
        sample['t'] / 1000, tz=UTC if timezone.is_aware(like) else None
    )


def steady(key, start, count, *, value=1.0, step=CADENCE):
    """``count`` readings for ``key`` at the source cadence from ``start``."""
    return [
        Reading(external_key=key, value=value + index, observed_at=start + step * index)
        for index in range(count)
    ]


class SeriesReadTest(HealthEnvMixin, TestCase):
    """Complete versus sampled reads, and what each one promises."""

    def setUp(self):
        """One machine, one historian source, one bound tag."""
        self.build_health_env()
        self.source.connector_type = _SeriesHistorian.key
        self.source.save(update_fields=['connector_type'])
        self.now = timezone.now().replace(microsecond=0)
        _SeriesHistorian.canned = {}
        _SeriesHistorian.asked = []
        _SeriesHistorian.failure = None

    def read(self, *, minutes=None, hours=None, **kwargs):
        """Read the environment's binding over a window ending now."""
        span = timedelta(minutes=minutes or 0, hours=hours or 0)
        return read_series(
            self.machine,
            binding_ids=[self.binding.pk],
            start=self.now - span,
            end=self.now,
            now=self.now,
            **kwargs,
        )

    def test_a_short_window_is_read_whole(self):
        """Ten minutes holds fewer snapshots than the complete-read bound."""
        start = self.now - timedelta(minutes=10)
        _SeriesHistorian.canned = {self.binding.external_key: steady(
            self.binding.external_key, start, 120
        )}

        result = self.read(minutes=10, points=50)

        self.assertEqual(result['mode'], MODE_COMPLETE)
        self.assertIsNone(result['slots'])
        [series] = result['series']
        self.assertTrue(series['available'])
        # Every reading, not fifty of them: a complete read ignores `points`.
        self.assertEqual(series['count'], 120)
        self.assertEqual(result['cadence_seconds'], EXPECTED_SAMPLE_INTERVAL_SECONDS)
        self.assertEqual(result['resolution_seconds'], EXPECTED_SAMPLE_INTERVAL_SECONDS)
        self.assertEqual(result['documents_read'], 120)

    def test_the_complete_read_bound_is_where_sampling_begins(self):
        """One snapshot past the bound switches the mode, not the data."""
        exact = COMPLETE_READ_MAX_DOCUMENTS * EXPECTED_SAMPLE_INTERVAL_SECONDS
        at_bound = read_series(
            self.machine,
            binding_ids=[self.binding.pk],
            start=self.now - timedelta(seconds=exact),
            end=self.now,
            now=self.now,
        )
        past_bound = read_series(
            self.machine,
            binding_ids=[self.binding.pk],
            start=self.now - timedelta(seconds=exact + EXPECTED_SAMPLE_INTERVAL_SECONDS),
            end=self.now,
            now=self.now,
        )
        self.assertEqual(at_bound['mode'], MODE_COMPLETE)
        self.assertEqual(past_bound['mode'], MODE_SAMPLED)

    def test_a_long_window_returns_one_real_reading_per_slot(self):
        """Six hours at 60 points is 60 readings, each one the source's own."""
        start = self.now - timedelta(hours=6)
        readings = steady(self.binding.external_key, start, 6 * 720)
        _SeriesHistorian.canned = {self.binding.external_key: readings}

        result = self.read(hours=6, points=60)

        self.assertEqual(result['mode'], MODE_SAMPLED)
        self.assertEqual(result['slots'], 60)
        self.assertEqual(result['resolution_seconds'], 360.0)
        [series] = result['series']
        self.assertEqual(series['count'], 60)

        # Each point is a reading the source actually returned, at the moment
        # it said, and it is the first one in its slot.
        held = {r.observed_at: r.value for r in readings}
        edges = slot_edges(start, self.now, 60)
        for (slot_start, _slot_end), sample in zip(edges, series['samples']):
            moment = moment_of(sample, self.now)
            self.assertIn(moment, held)
            self.assertEqual(sample['v'], held[moment])
            self.assertGreaterEqual(moment, slot_start)
            self.assertLess(moment - slot_start, CADENCE)

    def test_a_gap_in_the_source_stays_a_gap(self):
        """Slots with nothing in them contribute nothing - no fill, no carry."""
        start = self.now - timedelta(hours=3)
        first_hour = steady(self.binding.external_key, start, 720)
        third_hour = steady(
            self.binding.external_key, start + timedelta(hours=2), 720, value=500.0
        )
        _SeriesHistorian.canned = {self.binding.external_key: first_hour + third_hour}

        result = self.read(hours=3, points=30)

        [series] = result['series']
        # Ten slots an hour; the middle hour is missing from the source.
        self.assertEqual(series['count'], 20)
        stamps = [sample['t'] for sample in series['samples']]
        gap = max(later - earlier for earlier, later in zip(stamps, stamps[1:]))
        self.assertGreaterEqual(gap, timedelta(hours=1).total_seconds() * 1000)
        self.assertTrue(all(sample['v'] is not None for sample in series['samples']))

    def test_a_window_longer_than_a_day_is_refused(self):
        """The bound is the server's, and it is stated before any read."""
        with self.assertRaises(SeriesError) as caught:
            self.read(hours=MAX_SERIES_WINDOW_SECONDS // 3600 + 1)
        self.assertEqual(caught.exception.code, 'WINDOW_TOO_LONG')
        self.assertEqual(_SeriesHistorian.asked, [])

    def test_a_reversed_window_is_refused(self):
        """An end before its start is a request that means nothing."""
        with self.assertRaises(SeriesError):
            read_series(
                self.machine,
                binding_ids=[self.binding.pk],
                start=self.now,
                end=self.now - timedelta(hours=1),
                now=self.now,
            )

    def test_points_are_clamped_to_the_service_bounds(self):
        """Too few points draws nothing useful; too many is a full read in disguise."""
        start = self.now - timedelta(hours=6)
        _SeriesHistorian.canned = {self.binding.external_key: steady(
            self.binding.external_key, start, 6 * 720
        )}
        self.assertEqual(self.read(hours=6, points=1)['slots'], MIN_SERIES_POINTS)
        self.assertEqual(self.read(hours=6, points=10_000)['slots'], MAX_SERIES_POINTS)
        with self.assertRaises(SeriesError):
            self.read(hours=6, points='many')

    def test_a_source_without_a_native_sampler_cannot_cover_a_day(self):
        """The default sampler is a full read, and a full read stops at six hours.

        Reported per binding as its own reason, so the page can say the *source*
        cannot serve the window rather than that the request was wrong.
        """
        result = self.read(hours=12)
        [series] = result['series']
        self.assertFalse(series['available'])
        self.assertEqual(series['reason'], 'WINDOW_TOO_LONG')

    def test_a_status_code_is_plotted_and_carried_raw(self):
        """"R" draws as a number the chart can place, and still says "R"."""
        status = MachineSignalBinding.objects.create(
            machine=self.machine,
            source=self.source,
            external_key='/pd/P1/st',
            display_name='Equipment status',
            signal_kind='status',
        )
        start = self.now - timedelta(minutes=5)
        _SeriesHistorian.canned = {
            '/pd/P1/st': [
                Reading(external_key='/pd/P1/st', value='R', observed_at=start),
                Reading(
                    external_key='/pd/P1/st', value='I', observed_at=start + CADENCE
                ),
            ]
        }
        result = read_series(
            self.machine,
            binding_ids=[status.pk],
            start=start,
            end=self.now,
            now=self.now,
        )
        [series] = result['series']
        self.assertEqual(
            [(sample['v'], sample['raw']) for sample in series['samples']],
            [(1.0, 'R'), (0.0, 'I')],
        )
        # A plain number carries no `raw`: the value drawn is the value said.
        _SeriesHistorian.canned = {self.binding.external_key: steady(
            self.binding.external_key, start, 3
        )}
        [numeric] = self.read(minutes=5)['series']
        self.assertNotIn('raw', numeric['samples'][0])

    def test_limits_ride_along_so_a_chart_can_draw_them(self):
        """The bounds configured on the binding are the chart's reference lines."""
        [series] = self.read(minutes=5)['series']
        self.assertEqual(series['limits']['warn_max'], 6.0)
        self.assertEqual(series['limits']['critical_max'], 9.0)
        self.assertIsNone(series['limits']['warn_min'])

    def test_a_source_failure_is_reported_per_binding(self):
        """An outage is not a data point, and a timeout is named as one."""
        _SeriesHistorian.failure = RuntimeError('boom')
        [series] = self.read(minutes=5)['series']
        self.assertFalse(series['available'])
        self.assertEqual(series['reason'], 'SOURCE_UNAVAILABLE')

        _SeriesHistorian.failure = _TimeoutError('slow')
        [series] = self.read(minutes=5)['series']
        self.assertEqual(series['reason'], 'SOURCE_TIMEOUT')

    def test_a_source_with_no_connector_says_so(self):
        """An unregistered connector type is unconfigured, not empty."""
        self.source.connector_type = 'nothing-registered-here'
        self.source.save(update_fields=['connector_type'])
        [series] = self.read(minutes=5)['series']
        self.assertEqual(series['reason'], 'NO_CONNECTOR')

    def test_an_unknown_binding_is_absent_rather_than_read(self):
        """An id outside the machine's scope never reaches a connector."""
        result = read_series(
            self.machine,
            binding_ids=[self.binding.pk + 1000],
            start=self.now - timedelta(minutes=5),
            end=self.now,
            now=self.now,
        )
        self.assertEqual(result['series'], [])
        self.assertEqual(_SeriesHistorian.asked, [])

    def test_the_default_window_is_an_hour_ending_now(self):
        """An unparameterised read gets a bounded, recent window."""
        result = read_series(self.machine, binding_ids=[self.binding.pk], now=self.now)
        self.assertEqual(result['window_seconds'], 3600)
        self.assertEqual(
            datetime.fromisoformat(result['window_end']), self.now
        )


class SeriesScopeTest(TestCase):
    """A series may reach the machine's station and bays, and nothing beyond."""

    def setUp(self):
        """A station with two bays, each with one bound tag, plus the station's own."""
        self.tenant = Client.objects.create(name='Series', code=f'series-{uuid4().hex[:6]}')
        self.station = register_station(
            client=self.tenant,
            name='Station',
            source_namespace='series',
            source_entity_uuid=uuid4(),
            source_key='PH_SERIES',
        )
        self.pump_a = ensure_pump(self.station, 'P1')
        self.pump_b = ensure_pump(self.station, 'P2')
        self.source = HealthSource.objects.create(
            name='Series source',
            client=self.tenant,
            source_type=SourceType.SCADA,
            connector_type=_SeriesHistorian.key,
        )

        def bind(machine, key):
            return MachineSignalBinding.objects.create(
                machine=machine,
                source=self.source,
                external_key=key,
                display_name=key,
                unit='MW',
            )

        self.forebay = bind(self.station, '/dex/COMMAN_FORBAY_LEVEL')
        self.power_a = bind(self.pump_a, '/dex/PUMP1_ACTIVE_POWER')
        self.power_b = bind(self.pump_b, '/dex/PUMP2_ACTIVE_POWER')
        self.now = timezone.now().replace(microsecond=0)
        _SeriesHistorian.canned = {}
        _SeriesHistorian.asked = []
        _SeriesHistorian.failure = None

    def read(self, machine, **targets):
        """Read five minutes for ``machine`` naming the given targets."""
        result = read_series(
            machine,
            start=self.now - timedelta(minutes=5),
            end=self.now,
            now=self.now,
            **targets,
        )
        return {series['binding_id'] for series in result['series']}

    def test_a_station_reads_its_own_points_and_its_bays(self):
        """The station page compares bays, so their tags are in its scope."""
        found = self.read(
            self.station,
            binding_ids=[self.forebay.pk, self.power_a.pk, self.power_b.pk],
        )
        self.assertEqual(found, {self.forebay.pk, self.power_a.pk, self.power_b.pk})

    def test_a_pump_reads_its_own_points_and_its_station_context(self):
        """The forebay level is the context a pump operates in."""
        found = self.read(self.pump_a, binding_ids=[self.power_a.pk, self.forebay.pk])
        self.assertEqual(found, {self.power_a.pk, self.forebay.pk})

    def test_a_pump_cannot_read_a_sibling(self):
        """Another bay is another machine's telemetry."""
        found = self.read(self.pump_a, binding_ids=[self.power_a.pk, self.power_b.pk])
        self.assertEqual(found, {self.power_a.pk})

    def test_keys_resolve_inside_the_same_scope(self):
        """A mapped key is a convenience for the client, not a wider reach."""
        found = self.read(
            self.pump_a,
            keys=['/dex/PUMP1_ACTIVE_POWER', '/dex/PUMP2_ACTIVE_POWER', '/dex/NOT_A_TAG'],
        )
        self.assertEqual(found, {self.power_a.pk})

    def test_the_station_is_named_in_the_envelope(self):
        """A pump page needs its station's identity for context reads."""
        result = read_series(
            self.pump_a,
            binding_ids=[self.power_a.pk],
            start=self.now - timedelta(minutes=5),
            end=self.now,
            now=self.now,
        )
        self.assertEqual(result['station'], {'pk': self.station.pk, 'name': 'Station'})
        self.assertEqual(result['series'][0]['machine_id'], self.pump_a.pk)


class SeriesDisplayShiftTest(TestCase):
    """Recorded history reads as if it had just happened, and says so."""

    def setUp(self):
        """A station whose recorded range ended long before now."""
        self.tenant = Client.objects.create(name='Shift', code=f'shift-{uuid4().hex[:6]}')
        self.station = register_station(
            client=self.tenant,
            name='Recorded station',
            source_namespace='shift',
            source_entity_uuid=uuid4(),
            source_key='PH_SHIFT',
        )
        self.now = timezone.now().replace(microsecond=0)
        self.recorded_end = self.now - timedelta(days=400)
        self.source = HealthSource.objects.create(
            name='Recorded source',
            client=self.tenant,
            source_type=SourceType.HISTORIAN,
            connector_type=_SeriesHistorian.key,
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
            machine=self.station,
            source=self.source,
            external_key='/sl',
            display_name='Level',
            unit='m',
        )
        _SeriesHistorian.canned = {}
        _SeriesHistorian.asked = []
        _SeriesHistorian.failure = None

    def test_the_source_is_asked_in_its_own_clock_and_answers_in_ours(self):
        """The window moves back by the shift; the timestamps move forward by it."""
        shift = self.now - self.recorded_end
        source_start = self.recorded_end - timedelta(minutes=5)
        _SeriesHistorian.canned = {'/sl': steady('/sl', source_start, 3)}

        result = read_series(
            self.station,
            binding_ids=[self.binding.pk],
            start=self.now - timedelta(minutes=5),
            end=self.now,
            now=self.now,
        )

        self.assertTrue(result['display_shifted'])
        self.assertEqual(result['display_shift_seconds'], int(shift.total_seconds()))
        [(asked_start, asked_end)] = _SeriesHistorian.asked
        self.assertEqual(asked_start, source_start)
        self.assertEqual(asked_end, self.recorded_end)
        [series] = result['series']
        first = moment_of(series['samples'][0], self.now)
        self.assertEqual(first, source_start + shift)


class SeriesApiTest(HealthEnvMixin, InvenTreeAPITestCase):
    """The endpoint validates before it reads, and the signals list names keys."""

    roles = ['work_order.view']

    def setUp(self):
        """One machine with one bound tag on a historian source."""
        super().setUp()
        self.build_health_env()
        self.source.connector_type = _SeriesHistorian.key
        self.source.save(update_fields=['connector_type'])
        self.now = timezone.now().replace(microsecond=0)
        self.set_signal(3.0, observed_at=self.now)
        _SeriesHistorian.canned = {self.binding.external_key: steady(
            self.binding.external_key, self.now - timedelta(minutes=5), 60
        )}
        _SeriesHistorian.asked = []
        _SeriesHistorian.failure = None

    def url(self, suffix=''):
        """Return a machine-nested health URL."""
        return f'/api/machine-health/machines/{self.machine.pk}/health/{suffix}'

    def test_targets_are_required(self):
        """A series of nothing is a mistake, not an empty answer."""
        response = self.get(self.url('series/'), expected_code=400)
        self.assertEqual(response.data['code'], 'TARGETS_REQUIRED')

    def test_binding_ids_must_be_numeric(self):
        """A tag name in the id field is refused, not looked up."""
        response = self.get(
            self.url('series/'), {'bindings': '/dex/PUMP1_SPEED'}, expected_code=400
        )
        self.assertEqual(response.data['code'], 'BINDINGS_INVALID')

    def test_a_window_over_the_limit_is_400(self):
        """The service's bound surfaces as a client error with its code."""
        response = self.get(
            self.url('series/'),
            {
                'bindings': str(self.binding.pk),
                'from': (self.now - timedelta(hours=30)).isoformat(),
                'to': self.now.isoformat(),
            },
            expected_code=400,
        )
        self.assertEqual(response.data['code'], 'WINDOW_TOO_LONG')

    def test_a_series_carries_everything_the_page_needs(self):
        """Mode, resolution, liveness, limits and the samples themselves."""
        response = self.get(
            self.url('series/'),
            {
                'bindings': str(self.binding.pk),
                'from': (self.now - timedelta(minutes=5)).isoformat(),
                'to': self.now.isoformat(),
            },
            expected_code=200,
        )
        data = response.data
        self.assertEqual(data['mode'], MODE_COMPLETE)
        self.assertIn('poll_interval_seconds', data['live'])
        self.assertEqual(
            data['limits']['expected_sample_interval_seconds'],
            EXPECTED_SAMPLE_INTERVAL_SECONDS,
        )
        [series] = data['series']
        self.assertEqual(series['external_key'], self.binding.external_key)
        self.assertEqual(series['count'], 60)
        self.assertEqual(set(series['samples'][0]), {'t', 'v', 'q'})

    def test_keys_are_accepted_in_place_of_ids(self):
        """A page that knows the tag need not first look up the binding."""
        response = self.get(
            self.url('series/'),
            {
                'keys': self.binding.external_key,
                'from': (self.now - timedelta(minutes=5)).isoformat(),
                'to': self.now.isoformat(),
            },
            expected_code=200,
        )
        self.assertEqual(response.data['series'][0]['binding_id'], self.binding.pk)

    def test_signals_name_their_mapped_key(self):
        """The list a page groups by tag carries the tag."""
        response = self.get(self.url('signals/'), expected_code=200)
        [row] = response.data['results']
        self.assertEqual(row['external_key'], self.binding.external_key)
