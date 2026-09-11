"""Ingestion progress markers for polled, read-only telemetry sources.

A checkpoint records how far a source has been read, so a restarted worker
resumes instead of re-reading an hour bucket from its start. It holds position
only: no measurement, no payload and no credential ever lands here.

Position is expressed in the source's own coordinates - the hour bucket and the
sample timestamp within it - because those are what the query is built from. A
wall-clock timestamp would not be enough: the source clock, not ours, orders the
data.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

#: One hour of source samples, in milliseconds. A bucket is half-open:
#: ``hour_bucket <= sub_time_period < hour_bucket + HOUR_MS``.
HOUR_MS = 3_600_000


class IngestionCheckpoint(models.Model):
    """How far one source has been read for one station.

    Movement is forward-only. Rewinding a checkpoint would re-present samples the
    application has already accepted, and replayed history arriving as current
    state is precisely what the ingestion path is built to refuse; an operator who
    genuinely needs a re-read deletes the row rather than editing it backwards.
    """

    source = models.ForeignKey(
        'assets.HealthSource',
        on_delete=models.CASCADE,
        related_name='ingestion_checkpoints',
        verbose_name=_('Source'),
    )

    #: Station identity in the source system, opaque to the application. Kept as
    #: text rather than a foreign key: a checkpoint must survive a station that
    #: has not been registered yet, and must never imply the station exists here.
    station_uuid = models.CharField(
        max_length=64,
        db_index=True,
        help_text=_('Station identifier in the source system'),
        verbose_name=_('Station UUID'),
    )

    #: Start of the hour bucket last read, epoch milliseconds. Text, because the
    #: source stores it as text and a bucket is an identifier we echo back into a
    #: query, not a number we do arithmetic on.
    hour_bucket = models.CharField(
        max_length=20,
        help_text=_('Hour bucket last read, epoch milliseconds'),
        verbose_name=_('Hour Bucket'),
    )

    #: Timestamp of the last sample accepted from that bucket, epoch milliseconds.
    sub_time_period = models.BigIntegerField(
        help_text=_('Last sample read within the bucket, epoch milliseconds'),
        verbose_name=_('Sample Timestamp'),
    )

    #: Opaque resume token for sources that provide one (a Cosmos change feed
    #: continuation, for example). Blank while polling by timestamp range.
    continuation_token = models.TextField(
        blank=True,
        help_text=_('Opaque provider resume token; never a credential'),
        verbose_name=_('Continuation Token'),
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """One position per source and station."""

        ordering = ['source_id', 'station_uuid']
        constraints = [
            models.UniqueConstraint(
                fields=['source', 'station_uuid'],
                name='assets_ingestion_checkpoint_unique',
            )
        ]
        verbose_name = _('Ingestion Checkpoint')
        verbose_name_plural = _('Ingestion Checkpoints')

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'{self.source_id}/{self.station_uuid} @ {self.sub_time_period}'

    @property
    def position(self) -> tuple[int, int]:
        """Ordering key for this checkpoint: ``(hour bucket, sample)``."""
        return int(self.hour_bucket), self.sub_time_period

    def clean(self) -> None:
        """Reject a position the source could not have produced."""
        try:
            bucket = int(self.hour_bucket)
        except (TypeError, ValueError):
            raise ValidationError({
                'hour_bucket': _('Hour bucket must be epoch milliseconds.')
            }) from None

        if not bucket <= self.sub_time_period < bucket + HOUR_MS:
            raise ValidationError({
                'sub_time_period': _('Sample timestamp is outside its hourly bucket.')
            })

    def advance_to(self, hour_bucket, sub_time_period: int) -> bool:
        """Move the checkpoint forward, or leave it exactly as it was.

        Returns whether the position changed. The candidate is validated before
        anything is assigned, so a refused advance leaves the instance untouched
        in memory as well as in the database - a caller that ignores the return
        value must not end up holding a position that was never stored.
        """
        candidate = (int(hour_bucket), int(sub_time_period))
        if candidate <= self.position:
            return False

        bucket, sample = candidate
        if not bucket <= sample < bucket + HOUR_MS:
            raise ValidationError({
                'sub_time_period': _('Sample timestamp is outside its hourly bucket.')
            })

        self.hour_bucket = str(bucket)
        self.sub_time_period = sample
        self.save(update_fields=['hour_bucket', 'sub_time_period', 'updated_at'])
        return True
