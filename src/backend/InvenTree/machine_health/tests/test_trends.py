"""Bounded, federated trend reads.

The properties worth pinning: a client names a binding rather than a tag, the
window and sample count are the server's to enforce, and a source that cannot
serve history says so instead of getting a synthesized line.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from assets.models import AssetMachine
from InvenTree.unit_test import InvenTreeAPITestCase
from machine_health.connectors.base import (
    MAX_TREND_SAMPLES,
    MAX_TREND_WINDOW_SECONDS,
    HealthConnector,
    Reading,
    register,
)
from machine_health.services.trends import TrendError, read_trend

from .fixtures import HealthEnvMixin


@register
class _FakeHistorian(HealthConnector):
    """A connector that can serve history, for exercising the read path."""

    key = 'test-historian'

    #: Set by tests to control what the "remote platform" returns.
    canned_readings: list[Reading] = []
    last_external_key: str | None = None

    def check(self):
        """Always reachable."""
        return True, ''

    def read_latest(self, external_keys):
        """Not used by the trend path."""
        return []

    def read_window(self, external_key, start, end, *, max_samples=None):
        """Record the tag it was asked for, then return the canned window."""
        type(self).last_external_key = external_key
        return list(type(self).canned_readings)


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
        other = AssetMachine.objects.create(name='Foreign machine')
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
