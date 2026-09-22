"""Tests for normalized ingestion and its replay/bound protections."""

from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from assets.health_models import MachineSignalBinding, MachineSignalState, SignalQuality
from machine_health.services.ingestion import (
    MAX_READINGS_PER_BATCH,
    IngestionError,
    ingest_readings,
    record_source_error,
)

from .fixtures import HealthEnvMixin


class IngestReadingsTest(HealthEnvMixin, TestCase):
    """The single entry point every connector funnels through."""

    def setUp(self):
        """Build a machine with one bounded, mapped signal."""
        self.build_health_env()
        self.now = timezone.now()

    def reading(self, **overrides):
        """Return one well-formed reading for the mapped tag."""
        entry = {
            'external_key': self.binding.external_key,
            'value': 3.2,
            'observed_at': self.now,
            'quality': SignalQuality.GOOD,
        }
        entry.update(overrides)
        return entry

    def test_a_batch_costs_a_fixed_number_of_queries(self):
        """Ingestion must not scale its round trips with the reading count.

        A real pumphouse snapshot is around 700 readings. Issuing a locking
        read and then a write for each one is ~1,400 round trips and was
        measured at 8.6s per snapshot, which is what capped the whole pipeline.
        The rows taken are the same; they are taken once.
        """
        extra = MachineSignalBinding.objects.create(
            machine=self.machine,
            source=self.source,
            external_key='tag-two',
            display_name='Second',
            signal_kind='temperature',
            unit='degC',
            active=True,
        )
        one = [self.reading()]
        many = [self.reading(), self.reading(external_key=extra.external_key)]

        with CaptureQueriesContext(connection) as first:
            ingest_readings(self.source, one, now=self.now)
        with CaptureQueriesContext(connection) as second:
            ingest_readings(
                self.source, many, now=self.now + timedelta(seconds=1)
            )

        # Twice the readings must not mean twice the queries.
        self.assertLessEqual(len(second), len(first) + 2)

    def test_two_readings_for_one_binding_keep_the_newer(self):
        """The older of two readings for the same tag in one batch is dropped.

        The read-modify-write loop got this for free: the first reading was
        saved, so the second compared against it. Batching has to reproduce it
        deliberately, by registering an unsaved row before the write.
        """
        older = self.reading(value=1.0, observed_at=self.now - timedelta(seconds=30))
        newer = self.reading(value=2.0, observed_at=self.now)

        result = ingest_readings(self.source, [newer, older], now=self.now)

        self.assertEqual(result.accepted, 1)
        self.assertEqual(result.replayed, 1)
        state = MachineSignalState.objects.get(binding=self.binding)
        self.assertEqual(state.value['value'], 2.0)
        self.assertEqual(state.observed_at, self.now)

    def test_an_updated_row_refreshes_updated_at(self):
        """bulk_update does not run auto_now, so the timestamp is set by hand."""
        ingest_readings(self.source, [self.reading()], now=self.now)
        first = MachineSignalState.objects.get(binding=self.binding).updated_at

        later = self.now + timedelta(minutes=5)
        ingest_readings(
            self.source, [self.reading(value=9.9, observed_at=later)], now=later
        )

        state = MachineSignalState.objects.get(binding=self.binding)
        self.assertEqual(state.value['value'], 9.9)
        self.assertGreater(state.updated_at, first)

    def test_mapped_reading_updates_current_state(self):
        """A mapped tag writes the binding's current value and freshness."""
        result = ingest_readings(self.source, [self.reading()], now=self.now)

        self.assertEqual(result.accepted, 1)
        state = MachineSignalState.objects.get(binding=self.binding)
        self.assertEqual(state.value['value'], 3.2)
        self.assertEqual(state.observed_at, self.now)
        self.assertTrue(state.payload_hash)

        self.source.refresh_from_db()
        self.assertEqual(self.source.last_success_at, self.now)

    def test_unmapped_tag_is_dropped_not_auto_created(self):
        """A source cannot invent machine signals by sending unknown tags."""
        result = ingest_readings(
            self.source, [self.reading(external_key='UNKNOWN.TAG')], now=self.now
        )

        self.assertEqual(result.accepted, 0)
        self.assertEqual(result.unmapped, 1)
        self.assertEqual(MachineSignalBinding.objects.count(), 1)
        self.assertFalse(MachineSignalState.objects.exists())

    def test_older_observation_does_not_overwrite_a_newer_one(self):
        """Out-of-order delivery cannot rewind the current state."""
        ingest_readings(self.source, [self.reading(value=5.0)], now=self.now)

        stale = self.reading(value=1.0, observed_at=self.now - timedelta(minutes=5))
        result = ingest_readings(self.source, [stale], now=self.now)

        self.assertEqual(result.replayed, 1)
        state = MachineSignalState.objects.get(binding=self.binding)
        self.assertEqual(state.value['value'], 5.0)

    def test_source_sequence_wins_over_timestamps_when_present(self):
        """A platform's own sequence is the authority on ordering."""
        ingest_readings(
            self.source, [self.reading(value=5.0, sequence=10)], now=self.now
        )

        replay = self.reading(
            value=1.0, sequence=10, observed_at=self.now + timedelta(seconds=30)
        )
        result = ingest_readings(self.source, [replay], now=self.now)

        self.assertEqual(result.replayed, 1)
        state = MachineSignalState.objects.get(binding=self.binding)
        self.assertEqual(state.value['value'], 5.0)

    def test_far_future_observation_is_rejected(self):
        """A misconfigured clock cannot pin a signal as permanently fresh."""
        future = self.reading(observed_at=self.now + timedelta(hours=2))
        result = ingest_readings(self.source, [future], now=self.now)

        self.assertEqual(result.rejected, 1)
        self.assertEqual(result.accepted, 0)
        self.assertFalse(MachineSignalState.objects.exists())

    def test_batch_size_is_bounded(self):
        """One request cannot carry an unbounded batch."""
        oversized = [self.reading() for _ in range(MAX_READINGS_PER_BATCH + 1)]
        with self.assertRaisesMessage(IngestionError, 'at most'):
            ingest_readings(self.source, oversized, now=self.now)

    def test_transform_is_applied_before_storage(self):
        """Scale and offset land in the stored normalized value."""
        self.binding.transform = {'scale': 2, 'offset': 1}
        self.binding.save(update_fields=['transform'])

        ingest_readings(self.source, [self.reading(value=3.0)], now=self.now)

        state = MachineSignalState.objects.get(binding=self.binding)
        self.assertEqual(state.value['value'], 7.0)

    def test_malformed_reading_rejects_the_whole_batch(self):
        """A bad entry fails the request rather than writing a partial batch."""
        with self.assertRaises(IngestionError):
            ingest_readings(
                self.source,
                [self.reading(), {'value': 1, 'observed_at': self.now}],
                now=self.now,
            )
        self.assertFalse(MachineSignalState.objects.exists())

    def test_recorded_error_keeps_only_a_redacted_code(self):
        """Connector failures never store a provider message."""
        record_source_error(self.source, 'TIMEOUT', now=self.now)

        self.source.refresh_from_db()
        self.assertEqual(self.source.last_error_code, 'TIMEOUT')
        self.assertEqual(self.source.last_error_at, self.now)
        self.assertFalse(self.source.connection_healthy)
