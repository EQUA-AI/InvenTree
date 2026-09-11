"""Ingestion checkpoints record position only, and only ever move forward."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from InvenTree.unit_test import InvenTreeTestCase

from .models import HealthSource, IngestionCheckpoint

STATION = 'bafc976f-1ccc-4a91-aaa6-c3eac2470d36'
BUCKET = '1752850800000'
SAMPLE = 1752854398616


class IngestionCheckpointTests(InvenTreeTestCase):
    """Position bookkeeping for a polled, read-only source."""

    def setUp(self):
        """Create a source to hang checkpoints from."""
        super().setUp()
        self.source = HealthSource.objects.create(
            name='PH_3 Cosmos', source_type='scada', connector_type='cosmos_pumphouse'
        )

    def checkpoint(self, bucket=BUCKET, sample=SAMPLE):
        """Build a checkpoint at a given position."""
        return IngestionCheckpoint.objects.create(
            source=self.source,
            station_uuid=STATION,
            hour_bucket=bucket,
            sub_time_period=sample,
        )

    def test_position_must_sit_inside_its_bucket(self):
        """A sample outside the half-open hour is not a position we could have read."""
        bucket = int(BUCKET)
        for sample in [bucket - 1, bucket + 3_600_000]:
            with self.subTest(sample=sample):
                point = IngestionCheckpoint(
                    source=self.source,
                    station_uuid=STATION,
                    hour_bucket=BUCKET,
                    sub_time_period=sample,
                )
                with self.assertRaises(ValidationError):
                    point.full_clean()

        # The bucket start itself is a legitimate sample: the interval is
        # half-open at the top, not at the bottom.
        edge = IngestionCheckpoint(
            source=self.source,
            station_uuid=STATION,
            hour_bucket=BUCKET,
            sub_time_period=bucket,
        )
        edge.full_clean()

    def test_non_numeric_bucket_is_rejected(self):
        """An hour bucket that is not epoch milliseconds cannot be queried back."""
        point = IngestionCheckpoint(
            source=self.source,
            station_uuid=STATION,
            hour_bucket='not-a-bucket',
            sub_time_period=SAMPLE,
        )
        with self.assertRaises(ValidationError):
            point.full_clean()

    def test_advance_moves_forward_only(self):
        """Forward moves are stored; equal or earlier positions are refused."""
        point = self.checkpoint()
        # The pilot sample sits 1,384 ms before the end of its hour, so a step
        # must stay inside the bucket to be a legitimate next position.
        later = SAMPLE + 1000

        self.assertTrue(point.advance_to(BUCKET, later))
        self.assertEqual(point.sub_time_period, later)

        # Same position, and a sample already consumed: both must leave the
        # stored position untouched, so a re-poll cannot replay history as
        # current state.
        for sample in [later, SAMPLE, int(BUCKET)]:
            with self.subTest(sample=sample):
                self.assertFalse(point.advance_to(BUCKET, sample))

        point.refresh_from_db()
        self.assertEqual(point.position, (int(BUCKET), later))

    def test_advance_will_not_leave_its_bucket(self):
        """A sample past the hour's end belongs to the next bucket, not this one."""
        point = self.checkpoint()
        with self.assertRaises(ValidationError):
            point.advance_to(BUCKET, int(BUCKET) + 3_600_000)

        # Refused in memory as well as in the database: a caller that ignores
        # the exception must not be left holding an unstored position.
        self.assertEqual(point.sub_time_period, SAMPLE)
        point.refresh_from_db()
        self.assertEqual(point.sub_time_period, SAMPLE)

    def test_advance_crosses_into_the_next_bucket(self):
        """A new hour is a forward move even though its sample number restarts low."""
        point = self.checkpoint(sample=int(BUCKET) + 3_599_999)
        next_bucket = str(int(BUCKET) + 3_600_000)

        self.assertTrue(point.advance_to(next_bucket, int(next_bucket)))
        point.refresh_from_db()
        self.assertEqual(point.hour_bucket, next_bucket)

        # ... and the previous hour is now behind us.
        self.assertFalse(point.advance_to(BUCKET, int(BUCKET) + 3_599_999))

    def test_one_position_per_source_and_station(self):
        """A source cannot hold two positions for the same station."""
        self.checkpoint()
        with transaction.atomic(), self.assertRaises(IntegrityError):
            self.checkpoint(sample=SAMPLE + 1)

    def test_checkpoint_stores_no_measurement(self):
        """The model carries position and a resume token, never readings."""
        fields = {field.name for field in IngestionCheckpoint._meta.get_fields()}
        self.assertEqual(
            fields,
            {
                'id',
                'source',
                'station_uuid',
                'hour_bucket',
                'sub_time_period',
                'continuation_token',
                'updated_at',
            },
        )
