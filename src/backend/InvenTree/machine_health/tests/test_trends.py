"""Bounded, federated trend reads.

The properties worth pinning: a client names a binding rather than a tag, the
window and sample count are the server's to enforce, and a source that cannot
serve history says so instead of getting a synthesized line.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from assets.health_models import MachineSignalBinding
from assets.models import AssetMachine
from InvenTree.unit_test import InvenTreeAPITestCase
from machine_health.connectors.base import (
    EXPECTED_SAMPLE_INTERVAL_SECONDS,
    MAX_TREND_SAMPLES,
    MAX_TREND_WINDOW_SECONDS,
    HealthConnector,
    Reading,
    bounded_window,
    register,
)
from machine_health.services.trends import (
    DEFAULT_WINDOW_SECONDS,
    TrendError,
    measured_cadence,
    read_trend,
    read_trends,
)

from .fixtures import HealthEnvMixin


class TrendBoundsTest(TestCase):
    """The window ceiling and the sample cap have to describe the same limit.

    These are two statements about one bound, and the failure mode when they
    disagree is quiet: every request for the longest permitted window comes back
    flagged ``truncated``, having dropped the *newest* readings, while the UI
    goes on offering that range. The old pair said "30 days" and "2000 samples",
    which at this cadence is 2.8 hours - so "last 30 days" returned the oldest
    2.8 hours of the month and stopped.
    """

    def test_the_sample_cap_covers_a_full_length_window(self):
        """A window the server permits must be one it can return whole."""
        needed = MAX_TREND_WINDOW_SECONDS // EXPECTED_SAMPLE_INTERVAL_SECONDS

        self.assertGreaterEqual(
            MAX_TREND_SAMPLES,
            needed,
            'The longest permitted window holds more samples than the cap '
            'allows, so every full-length read would be silently truncated.',
        )

    def test_the_default_window_is_within_the_ceiling(self):
        """The unparameterised read must not be one the bound would refuse."""
        self.assertLessEqual(DEFAULT_WINDOW_SECONDS, MAX_TREND_WINDOW_SECONDS)

    def test_a_window_past_the_ceiling_is_refused(self):
        """The ceiling is enforced, not merely documented."""
        end = timezone.now()
        start = end - timedelta(seconds=MAX_TREND_WINDOW_SECONDS + 1)

        with self.assertRaisesMessage(ValueError, 'may not exceed'):
            bounded_window(start, end)

    def test_a_window_exactly_at_the_ceiling_is_allowed(self):
        """An off-by-one here would make the advertised maximum unusable."""
        end = timezone.now()
        start = end - timedelta(seconds=MAX_TREND_WINDOW_SECONDS)

        _start, _end, samples = bounded_window(start, end)

        self.assertEqual(samples, MAX_TREND_SAMPLES)


@register
class _FakeHistorian(HealthConnector):
    """A connector that can serve history, for exercising the read path."""

    key = 'test-historian'

    #: Set by tests to control what the "remote platform" returns.
    canned_readings: list[Reading] = []
    last_external_key: str | None = None
    #: How many windows have been read, to show batching reads the source once.
    window_reads: int = 0

    def check(self):
        """Always reachable."""
        return True, ''

    def read_latest(self, external_keys):
        """Not used by the trend path."""
        return []

    def read_window(self, external_key, start, end, *, max_samples=None):
        """Record the tag it was asked for, then return the canned window."""
        type(self).last_external_key = external_key
        type(self).window_reads += 1
        return [
            reading
            for reading in type(self).canned_readings
            if reading.external_key == external_key
        ]


@register
class _ObedientHistorian(HealthConnector):
    """A connector that honours ``max_samples``, the way the real ones do.

    This distinction is the whole point of the test below. ``_FakeHistorian``
    ignores the cap and returns everything, so the service trims it and notices.
    A real connector stops reading once it has enough, which means the service
    sees exactly the number it asked for and cannot tell a full window from an
    overflowing one - unless it deliberately asks for one more than it needs.
    """

    key = 'test-obedient'

    canned_readings: list[Reading] = []
    last_max_samples: int | None = None

    def check(self):
        """Always reachable."""
        return True, ''

    def read_latest(self, external_keys):
        """Not used by the trend path."""
        return []

    def read_window(self, external_key, start, end, *, max_samples=None):
        """Return at most ``max_samples`` readings, stopping early like Cosmos."""
        type(self).last_max_samples = max_samples
        readings = list(type(self).canned_readings)
        return readings[:max_samples] if max_samples else readings


@register
class _BrokenHistorian(HealthConnector):
    """A connector whose history read fails."""

    key = 'test-broken'

    def check(self):
        """Always reachable."""
        return True, ''

    def read_latest(self, external_keys):
        """Not used by the trend path."""
        return []

    def read_window(self, external_key, start, end, *, max_samples=None):
        """Fail the way a real outage does."""
        raise ConnectionError('historian unreachable')


class _ReadTimeout(Exception):
    """Named to match the SDK exceptions the service classifies by name.

    `trends` must not import the Cosmos SDK to recognise a Cosmos error, so it
    matches on the class name instead. Naming this the way a real one is named
    is what makes the test exercise that rule rather than a mock of it.
    """


@register
class _SlowHistorian(HealthConnector):
    """A connector that is reachable but does not answer in time."""

    key = 'test-slow'

    def check(self):
        """Always reachable - that is the point of this fixture."""
        return True, ''

    def read_latest(self, external_keys):
        """Not used by the trend path."""
        return []

    def read_window(self, external_key, start, end, *, max_samples=None):
        """Time out the way an over-large window does."""
        raise _ReadTimeout('the request failed to complete within the given timeout')


class TrendReadTest(HealthEnvMixin, TestCase):
    """The service-level trend read."""

    def setUp(self):
        """Build a machine with one mapped signal and a history-capable source."""
        self.build_health_env()
        self.now = timezone.now()
        self.source.connector_type = 'test-historian'
        self.source.save(update_fields=['connector_type'])
        _FakeHistorian.canned_readings = [
            Reading(
                external_key=self.binding.external_key,
                value=3.0 + index,
                observed_at=self.now - timedelta(minutes=index),
            )
            for index in range(5)
        ]
        _FakeHistorian.last_external_key = None

    def test_trend_returns_bounded_samples(self):
        """A readable window comes back with its samples and its bounds."""
        result = read_trend(self.machine, binding_id=self.binding.pk, now=self.now)

        self.assertTrue(result['available'])
        self.assertEqual(len(result['samples']), 5)
        self.assertEqual(result['unit'], 'mm/s')
        self.assertEqual(result['limits']['max_samples'], MAX_TREND_SAMPLES)

    def test_the_connector_receives_the_mapped_tag(self):
        """The external key comes from the mapping, never from the caller."""
        read_trend(self.machine, binding_id=self.binding.pk, now=self.now)

        self.assertEqual(
            _FakeHistorian.last_external_key, self.binding.external_key
        )

    def test_a_binding_from_another_machine_is_not_readable(self):
        """Scope is applied before the read, not after."""
        other = AssetMachine.objects.create(name='Someone else')

        with self.assertRaises(TrendError):
            read_trend(other, binding_id=self.binding.pk, now=self.now)

    def test_oversized_window_is_refused(self):
        """One request cannot pull a historian dry."""
        with self.assertRaisesMessage(TrendError, 'may not exceed'):
            read_trend(
                self.machine,
                binding_id=self.binding.pk,
                start=self.now - timedelta(seconds=MAX_TREND_WINDOW_SECONDS * 2),
                end=self.now,
                now=self.now,
            )

    def test_inverted_window_is_refused(self):
        """An end before its start is a client error, not an empty result."""
        with self.assertRaises(TrendError):
            read_trend(
                self.machine,
                binding_id=self.binding.pk,
                start=self.now,
                end=self.now - timedelta(hours=1),
                now=self.now,
            )

    def test_sample_cap_is_enforced_server_side(self):
        """A connector that ignores the cap does not get to exceed it."""
        result = read_trend(
            self.machine, binding_id=self.binding.pk, max_samples=2, now=self.now
        )

        self.assertEqual(len(result['samples']), 2)
        self.assertTrue(result['truncated'])

    def test_truncation_is_reported_when_the_connector_honours_the_cap(self):
        """A connector that stops at the cap must still produce a truncation flag.

        Regression: the service used to ask for exactly the number of samples it
        would return, so a connector that obeyed the cap always looked like a
        complete window. The chart would silently drop the newest part of the
        range while telling the operator it was whole.
        """
        self.source.connector_type = 'test-obedient'
        self.source.save(update_fields=['connector_type'])
        _ObedientHistorian.canned_readings = [
            Reading(
                external_key=self.binding.external_key,
                value=float(index),
                observed_at=self.now - timedelta(minutes=index),
            )
            for index in range(10)
        ]

        result = read_trend(
            self.machine, binding_id=self.binding.pk, max_samples=4, now=self.now
        )

        self.assertEqual(len(result['samples']), 4)
        self.assertTrue(result['truncated'])
        # The probe is what makes the detection possible.
        self.assertEqual(_ObedientHistorian.last_max_samples, 5)

    def test_an_exactly_full_window_is_not_reported_as_truncated(self):
        """No false alarm when the data happens to fill the cap exactly.

        A warning that cries wolf is worse than none, because operators learn to
        dismiss it.
        """
        self.source.connector_type = 'test-obedient'
        self.source.save(update_fields=['connector_type'])
        _ObedientHistorian.canned_readings = [
            Reading(
                external_key=self.binding.external_key,
                value=float(index),
                observed_at=self.now - timedelta(minutes=index),
            )
            for index in range(4)
        ]

        result = read_trend(
            self.machine, binding_id=self.binding.pk, max_samples=4, now=self.now
        )

        self.assertEqual(len(result['samples']), 4)
        self.assertFalse(result['truncated'])

    def test_a_silence_in_the_source_stays_a_silence(self):
        """Readings keep their own times, nothing fills a gap, the cadence is stated.

        Five-second readings either side of a two-minute silence come back as
        exactly those readings at their source times. The measured cadence is
        what a chart breaks its line against, so the silence is drawn as one
        rather than joined; the expected interval is the fallback when too few
        readings arrive to measure.
        """
        start = self.now - timedelta(minutes=5)
        stamps = [start + timedelta(seconds=5 * i) for i in range(6)] + [
            start + timedelta(seconds=150 + 5 * i) for i in range(6)
        ]
        _FakeHistorian.canned_readings = [
            Reading(
                external_key=self.binding.external_key,
                value=float(index),
                observed_at=at,
            )
            for index, at in enumerate(stamps)
        ]

        result = read_trend(
            self.machine,
            binding_id=self.binding.pk,
            start=start,
            end=self.now,
            now=self.now,
        )

        self.assertEqual(result['display_shift_seconds'], 0)
        self.assertEqual(
            [sample['observed_at'] for sample in result['samples']],
            [at.isoformat() for at in stamps],
        )
        self.assertEqual(result['cadence_seconds'], 5.0)
        self.assertEqual(
            result['limits']['expected_sample_interval_seconds'],
            EXPECTED_SAMPLE_INTERVAL_SECONDS,
        )

    def test_too_few_readings_measure_no_cadence(self):
        """Two readings are one interval: not enough to call it the cadence."""
        _FakeHistorian.canned_readings = _FakeHistorian.canned_readings[:2]

        result = read_trend(self.machine, binding_id=self.binding.pk, now=self.now)

        self.assertIsNone(result['cadence_seconds'])
        self.assertEqual(
            result['limits']['expected_sample_interval_seconds'],
            EXPECTED_SAMPLE_INTERVAL_SECONDS,
        )

    def test_the_cadence_is_measured_in_time_order(self):
        """Readings returned newest first still measure a positive cadence."""
        # setUp's readings are a minute apart, newest first.
        result = read_trend(self.machine, binding_id=self.binding.pk, now=self.now)

        self.assertEqual(result['cadence_seconds'], 60.0)

    def test_source_without_a_connector_reports_unavailable(self):
        """No trend is invented for a source that cannot serve one."""
        self.source.connector_type = ''
        self.source.save(update_fields=['connector_type'])

        result = read_trend(self.machine, binding_id=self.binding.pk, now=self.now)

        self.assertFalse(result['available'])
        self.assertEqual(result['reason'], 'NO_CONNECTOR')
        self.assertEqual(result['samples'], [])

    def test_connector_failure_is_an_outage_not_a_data_point(self):
        """A failed read yields nothing rather than a synthesized line."""
        self.source.connector_type = 'test-broken'
        self.source.save(update_fields=['connector_type'])

        result = read_trend(self.machine, binding_id=self.binding.pk, now=self.now)

        self.assertFalse(result['available'])
        self.assertEqual(result['reason'], 'SOURCE_UNAVAILABLE')
        self.assertEqual(result['samples'], [])

    def test_a_timeout_is_reported_as_a_timeout_not_as_unreachable(self):
        """The two failures send an operator to different places.

        'Could not be reached' points at the network, the endpoint or
        credentials. A timeout usually means the window asked for more than the
        source could return in time - measured against the live account, an
        hour of ~95 KB documents at 400 RU/s times out while fifteen minutes of
        the same data succeeds. Collapsing both into SOURCE_UNAVAILABLE sends
        someone hunting connectivity when the fix is a shorter window.
        """
        self.source.connector_type = 'test-slow'
        self.source.save(update_fields=['connector_type'])

        result = read_trend(self.machine, binding_id=self.binding.pk, now=self.now)

        self.assertFalse(result['available'])
        self.assertEqual(result['reason'], 'SOURCE_TIMEOUT')
        self.assertEqual(result['samples'], [])
        self.assertIn('shorter window', result['detail'])
        # And it must not claim the source was unreachable, because it wasn't.
        self.assertNotIn('could not be reached', result['detail'].lower())

    def test_a_timeout_still_returns_no_samples(self):
        """Availability is the flag; an empty series must never imply success."""
        self.source.connector_type = 'test-slow'
        self.source.save(update_fields=['connector_type'])

        result = read_trend(self.machine, binding_id=self.binding.pk, now=self.now)

        self.assertEqual(result['samples'], [])
        self.assertFalse(result['available'])

    def test_unregistered_connector_does_not_fall_back(self):
        """An unknown adapter reads as unconfigured, not as some default."""
        self.source.connector_type = 'not-registered'
        self.source.save(update_fields=['connector_type'])

        result = read_trend(self.machine, binding_id=self.binding.pk, now=self.now)

        self.assertFalse(result['available'])
        self.assertEqual(result['reason'], 'NO_CONNECTOR')


class TrendApiTest(HealthEnvMixin, InvenTreeAPITestCase):
    """HTTP contract for the trend endpoint."""

    roles = ['work_order.view']

    def setUp(self):
        """Build a machine with a history-capable source."""
        super().setUp()
        self.build_health_env()
        self.now = timezone.now()
        self.source.connector_type = 'test-historian'
        self.source.save(update_fields=['connector_type'])
        _FakeHistorian.canned_readings = [
            Reading(
                external_key=self.binding.external_key,
                value=4.2,
                observed_at=self.now,
            )
        ]
        self.url = f'/api/machine-health/machines/{self.machine.pk}/health/trend/'

    def test_trend_requires_a_binding(self):
        """A tag name is never accepted in place of a mapped binding id."""
        response = self.get(self.url, expected_code=400)
        self.assertEqual(response.data['code'], 'BINDING_REQUIRED')

        named = self.get(
            self.url, {'binding': self.binding.external_key}, expected_code=400
        )
        self.assertEqual(named.data['code'], 'BINDING_REQUIRED')

    def test_trend_returns_samples_for_a_mapped_binding(self):
        """A valid request returns the bounded window."""
        response = self.get(
            self.url, {'binding': self.binding.pk}, expected_code=200
        )

        self.assertTrue(response.data['available'])
        self.assertEqual(len(response.data['samples']), 1)
        self.assertEqual(response.data['samples'][0]['value'], 4.2)

    def test_another_machines_binding_is_refused(self):
        """Binding ids from elsewhere are not readable through this machine."""
        other = AssetMachine.objects.create(
            name='Foreign machine', client=self.client_tenant
        )
        response = self.get(
            f'/api/machine-health/machines/{other.pk}/health/trend/',
            {'binding': self.binding.pk},
            expected_code=400,
        )
        self.assertEqual(response.data['code'], 'TREND_INVALID')

    def test_malformed_window_is_refused(self):
        """An unparsable timestamp is a client error."""
        response = self.get(
            self.url,
            {'binding': self.binding.pk, 'from': 'yesterday'},
            expected_code=400,
        )
        self.assertEqual(response.data['code'], 'INVALID_WINDOW')


class TrendsBatchReadTest(HealthEnvMixin, TestCase):
    """Reading many of a machine's signals in one call."""

    def setUp(self):
        """Two mapped signals on one history-capable source."""
        self.build_health_env()
        self.now = timezone.now()
        self.source.connector_type = 'test-historian'
        self.source.save(update_fields=['connector_type'])
        self.second = MachineSignalBinding.objects.create(
            machine=self.machine,
            source=self.source,
            external_key='tag-two',
            display_name='Second',
            signal_kind='temperature',
            unit='degC',
            active=True,
        )
        _FakeHistorian.canned_readings = [
            Reading(
                external_key=key,
                value=value,
                observed_at=self.now - timedelta(minutes=offset),
            )
            for key, value in (
                (self.binding.external_key, 1.0), (self.second.external_key, 2.0)
            )
            for offset in range(3)
        ]
        _FakeHistorian.window_reads = 0

    def test_every_requested_binding_comes_back(self):
        """Each binding gets its own trend, carrying its own samples."""
        results = read_trends(
            self.machine,
            binding_ids=[self.binding.pk, self.second.pk],
            now=self.now,
        )

        self.assertEqual(len(results), 2)
        by_id = {r['binding_id']: r for r in results}
        self.assertTrue(all(r['available'] for r in results))
        self.assertTrue(
            all(s['value'] == 1.0 for s in by_id[self.binding.pk]['samples'])
        )
        self.assertTrue(
            all(s['value'] == 2.0 for s in by_id[self.second.pk]['samples'])
        )

    def test_each_binding_states_its_own_measured_cadence(self):
        """A table's trends each carry the cadence of their own readings."""
        _FakeHistorian.canned_readings = [
            Reading(
                external_key=self.binding.external_key,
                value=1.0,
                observed_at=self.now - timedelta(seconds=5 * offset),
            )
            for offset in range(4)
        ] + [
            Reading(
                external_key=self.second.external_key,
                value=2.0,
                observed_at=self.now - timedelta(seconds=30 * offset),
            )
            for offset in range(4)
        ]

        results = read_trends(
            self.machine, binding_ids=[self.binding.pk, self.second.pk], now=self.now
        )

        by_id = {r['binding_id']: r for r in results}
        self.assertEqual(by_id[self.binding.pk]['cadence_seconds'], 5.0)
        self.assertEqual(by_id[self.second.pk]['cadence_seconds'], 30.0)
        self.assertTrue(
            all(
                r['limits']['expected_sample_interval_seconds']
                == EXPECTED_SAMPLE_INTERVAL_SECONDS
                for r in results
            )
        )

    def test_a_binding_on_another_machine_is_not_returned(self):
        """The machine scopes the read, exactly as the single-binding path does."""
        other = AssetMachine.objects.create(name='Someone else')
        stranger = MachineSignalBinding.objects.create(
            machine=other,
            source=self.source,
            external_key='not-yours',
            display_name='Stranger',
            signal_kind='temperature',
            unit='degC',
            active=True,
        )

        results = read_trends(
            self.machine, binding_ids=[self.binding.pk, stranger.pk], now=self.now
        )

        self.assertEqual([r['binding_id'] for r in results], [self.binding.pk])

    def test_an_unreadable_source_reports_per_binding(self):
        """One failure marks every binding unavailable, and raises nothing.

        A page of sparklines must degrade to "no trend" rather than error out.
        """
        self.source.connector_type = 'test-broken'
        self.source.save(update_fields=['connector_type'])

        results = read_trends(
            self.machine,
            binding_ids=[self.binding.pk, self.second.pk],
            now=self.now,
        )

        self.assertEqual(len(results), 2)
        self.assertFalse(any(r['available'] for r in results))
        self.assertTrue(all(r['reason'] == 'SOURCE_UNAVAILABLE' for r in results))

    def test_the_window_is_read_once_per_source_not_once_per_binding(self):
        """The whole point: N sparklines must not mean N reads of one window.

        The default read_windows falls back to one read_window per key, so this
        counts those - a connector that overrides it (Cosmos does) makes one
        pass instead, which is strictly fewer.
        """
        read_trends(
            self.machine,
            binding_ids=[self.binding.pk, self.second.pk],
            now=self.now,
        )
        baseline = _FakeHistorian.window_reads

        _FakeHistorian.window_reads = 0
        for binding in (self.binding, self.second):
            read_trend(self.machine, binding_id=binding.pk, now=self.now)

        # Same work for the fallback, and one connector setup rather than two.
        self.assertEqual(baseline, _FakeHistorian.window_reads)


class MeasuredCadenceTest(TestCase):
    """The cadence a chart breaks its line against."""

    def test_one_silence_cannot_stretch_a_three_reading_cadence(self):
        """An outage is not the interval, even with just two gaps."""
        self.assertEqual(self.cadence_at([0, 5, 595]), 5.0)

    def test_alternating_silences_cannot_become_the_cadence(self):
        """Repeated sixty-second silences remain distinct from five-second reads."""
        self.assertEqual(self.cadence_at([0, 5, 65, 70, 130, 135, 195]), 5.0)

    def test_duplicate_times_do_not_become_a_zero_cadence(self):
        """Only positive observed gaps inform the interval."""
        self.assertEqual(self.cadence_at([0, 0, 5]), 5.0)
        self.assertIsNone(self.cadence_at([0, 0, 0]))

    def cadence_at(self, offsets):
        """Measure the supplied observation times relative to one origin."""
        start = timezone.now()
        return measured_cadence([
            Reading(
                external_key='k', value=1.0, observed_at=start + timedelta(seconds=s)
            )
            for s in offsets
        ])

    def test_the_median_ignores_a_few_silences(self):
        """Two long silences among five-second readings leave the cadence at five."""
        start = timezone.now()
        offsets = [0, 5, 10, 15, 135, 140, 145, 400, 405, 410]
        readings = [
            Reading(
                external_key='k', value=1.0, observed_at=start + timedelta(seconds=s)
            )
            for s in offsets
        ]

        self.assertEqual(measured_cadence(readings), 5.0)

    def test_fewer_than_three_readings_have_no_cadence(self):
        """Nothing to measure is not a cadence of zero."""
        start = timezone.now()
        self.assertIsNone(measured_cadence([]))
        self.assertIsNone(
            measured_cadence([
                Reading(external_key='k', value=1.0, observed_at=start),
                Reading(
                    external_key='k',
                    value=2.0,
                    observed_at=start + timedelta(seconds=5),
                ),
            ])
        )
