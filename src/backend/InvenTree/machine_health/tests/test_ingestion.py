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

    def test_a_webhook_cannot_call_an_over_range_marker_good(self):
        """The Cosmos path marks 3276.7 bad before it reaches a limit; this path did not.

        A sender says what quality it likes and said "good" of everything by
        default, so the over-range marker arriving this way would have been
        classified as a reading and tripped a critical alarm. The same rule,
        from the same function, now applies on the way in.
        """
        for value in (3276.7, -59.25925827026367, float('inf')):
            with self.subTest(value=value):
                MachineSignalState.objects.filter(binding=self.binding).delete()
                ingest_readings(self.source, [self.reading(value=value)], now=self.now)
                state = MachineSignalState.objects.get(binding=self.binding)
                self.assertEqual(state.quality, SignalQuality.BAD)

        # An ordinary number is as good as the sender said.
        MachineSignalState.objects.filter(binding=self.binding).delete()
        ingest_readings(self.source, [self.reading(value=42.0)], now=self.now)
        state = MachineSignalState.objects.get(binding=self.binding)
        self.assertEqual(state.quality, SignalQuality.GOOD)

        # And a sender's own doubt is kept, never upgraded.
        MachineSignalState.objects.filter(binding=self.binding).delete()
        ingest_readings(
            self.source,
            [self.reading(value=42.0, quality=SignalQuality.UNCERTAIN)],
            now=self.now,
        )
        state = MachineSignalState.objects.get(binding=self.binding)
        self.assertEqual(state.quality, SignalQuality.UNCERTAIN)

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


class ValueChangedAtTest(HealthEnvMixin, TestCase):
    """How long a reading has been the same number.

    Freshness cannot answer this. Timestamps advance on every poll whether or
    not the value moves, so a channel reporting punctually and saying the same
    thing for ever reads as perfectly current - which is exactly what an
    acquisition frozen at its last good sample looks like. Millbrook Pump 05
    holds one value on 35 of its 37 channels across the whole recorded window,
    including its run status and its active power.

    Recorded as a measurement rather than a verdict: a stopped bay reports
    MOTOR_ON_STATUS 0 for ever and is right to, so nothing here decides what an
    unchanging channel means.
    """

    def setUp(self):
        """One mapped signal and a plant clock to advance.

        The window runs backwards from the present. Ingestion rejects a reading
        dated in the future - correctly, since a snapshot that has not happened
        cannot be current state - so a test that advances past ``now`` is not
        testing the span, it is testing the skew guard.
        """
        self.build_health_env()
        self.now = timezone.now()
        self.started = self.now - timedelta(hours=12)

    def send(self, value, *, at):
        """Ingest one reading carrying its own plant timestamp."""
        ingest_readings(
            self.source,
            [
                {
                    'external_key': self.binding.external_key,
                    'value': value,
                    'observed_at': at,
                    'quality': SignalQuality.GOOD,
                }
            ],
        )
        return MachineSignalState.objects.get(binding=self.binding)

    def test_the_first_reading_sets_the_instant(self):
        """A channel seen once has changed once, as far as anyone can tell."""
        state = self.send(3.2, at=self.started)

        self.assertEqual(state.value_changed_at, self.started)

    def test_a_repeated_value_keeps_the_earlier_instant(self):
        """The span is what matters, so the start of it must not move."""
        self.send(3.2, at=self.started)

        state = self.send(3.2, at=self.started + timedelta(hours=6))

        self.assertEqual(state.value_changed_at, self.started)
        self.assertEqual(state.observed_at, self.started + timedelta(hours=6))
        self.assertEqual(
            (state.observed_at - state.value_changed_at), timedelta(hours=6)
        )

    def test_a_different_value_moves_it(self):
        """A genuine change restarts the span from the change."""
        self.send(3.2, at=self.started)
        moved_at = self.started + timedelta(hours=6)

        state = self.send(4.8, at=moved_at)

        self.assertEqual(state.value_changed_at, moved_at)

    def test_a_catch_up_of_identical_snapshots_does_not_reset_the_span(self):
        """The case the field exists for.

        A poll applying two hundred identical snapshots must leave the span
        measured from the last genuine change, not from the newest arrival -
        otherwise a frozen channel looks freshly changed after every poll.
        """
        self.send(3.2, at=self.started)
        for minute in range(1, 20):
            state = self.send(3.2, at=self.started + timedelta(minutes=minute))

        self.assertEqual(state.value_changed_at, self.started)
        self.assertEqual(
            (state.observed_at - state.value_changed_at), timedelta(minutes=19)
        )

    def test_returning_to_an_earlier_value_counts_as_a_change(self):
        """Compared against what was stored, not against everything ever seen.

        A channel that oscillates is moving, which is the opposite of the fault
        this measures, so each transition restarts the span.
        """
        self.send(3.2, at=self.started)
        self.send(4.8, at=self.started + timedelta(hours=1))
        back_at = self.started + timedelta(hours=2)

        state = self.send(3.2, at=back_at)

        self.assertEqual(state.value_changed_at, back_at)
