"""Read pumphouse snapshots from an Azure Cosmos DB (NoSQL) container.

This adapter reads and nothing else. Read-only is enforced twice over: the class
exposes no write path, and the identity it authenticates as holds the built-in
**Cosmos DB Data Reader** role, which Azure will not let write a document even if
this code asked it to. A bug here cannot corrupt the plant's record.

Three rules shape the implementation.

**Every query names a full partition key.** The container is partitioned
hierarchically on ``/station_uuid`` + ``/hour_bucket``, and
``enable_cross_partition_query`` is explicitly ``False``. A query missing either
component would fan out across every station and every hour in the account - a
cheap mistake to make and an expensive one to discover on a production bill - so
:func:`_partition_key` refuses to build one rather than letting the SDK widen the
scope on our behalf.

**Failures are reported as a code, never as a message.** Provider exceptions
carry endpoints, database ids and occasionally tokens. Only the classification in
:data:`ERROR_CODES` is returned, persisted or logged.

**A snapshot is ingested whole or not at all.** One real snapshot flattens to
roughly 760 readings against an ingestion limit of 500, so it always spans
several batches. The checkpoint therefore advances per *snapshot*, after every
one of its batches has been accepted - never per batch, which would record a
sample as fully read when half of it had failed.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from machine_health.connectors.base import (
    HealthConnector,
    Reading,
    bounded_window,
    register,
)
from machine_health.connectors.pumphouse_payload import (
    SnapshotError,
    flatten_snapshot,
    in_batches,
)

logger = logging.getLogger('inventree')

#: One hour of source samples, in milliseconds. Buckets are half-open:
#: ``hour_bucket <= sub_time_period < hour_bucket + HOUR_MS``.
HOUR_MS = 3_600_000

#: The whole vocabulary a failure may be reported as. Anything the provider says
#: is collapsed into one of these before it can reach a log line or a database
#: row. ``CONFIG`` covers a source that cannot be used as configured - calling
#: that ``NETWORK`` would send an operator to look at a firewall for a missing
#: setting.
ERROR_CODES = frozenset({'OK', 'AUTH', 'NOT_FOUND', 'THROTTLED', 'NETWORK', 'CONFIG'})

#: Page size for slice queries. Small enough that a slow account cannot hand back
#: an unbounded response in one round trip.
PAGE_SIZE = 100

#: Hour buckets a single window read may walk. The trend window is already capped
#: at 30 days by ``bounded_window``; this bounds the number of round trips that
#: window can turn into.
MAX_BUCKETS_PER_READ = 24 * 31

#: Hour buckets one poll may walk. A poller that has fallen days behind catches
#: up over several runs rather than issuing hundreds of queries in one tick and
#: starving every other source sharing the worker.
MAX_BUCKETS_PER_POLL = 6

#: Endpoints the local emulator is reachable on. A key may be used against these
#: and nowhere else.
EMULATOR_HOSTS = frozenset({'localhost', '127.0.0.1', '::1', 'cosmos-emulator'})

QUERY_LATEST = (
    'SELECT TOP 1 * FROM c '
    'WHERE c.station_uuid = @station AND c.hour_bucket = @bucket '
    'ORDER BY c.sub_time_period DESC'
)

QUERY_SLICE = (
    'SELECT * FROM c '
    'WHERE c.station_uuid = @station AND c.hour_bucket = @bucket '
    'AND c.sub_time_period >= @from_ts AND c.sub_time_period < @to_ts '
    'ORDER BY c.sub_time_period ASC'
)


class CosmosConfigError(Exception):
    """The source cannot be read as configured.

    Distinct from a provider failure: no request has been made and retrying will
    not help until someone changes the configuration.
    """


def bucket_of(moment_ms: int) -> int:
    """Return the hour bucket a sample timestamp belongs to."""
    return int(moment_ms) - int(moment_ms) % HOUR_MS


def to_epoch_ms(moment: datetime) -> int:
    """Convert an aware datetime to epoch milliseconds, truncating downward.

    Truncation is deliberate and downward: rounding a sample timestamp up could
    move it past a bucket boundary and hide it from the query that should find
    it.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def _classify(exc: Exception) -> str:
    """Reduce a provider exception to one code from :data:`ERROR_CODES`.

    The exception itself is never returned, logged or stored - only this code.
    """
    status = getattr(exc, 'status_code', None)
    if status in (401, 403):
        return 'AUTH'
    if status == 404:
        return 'NOT_FOUND'
    if status == 429:
        return 'THROTTLED'
    if status is not None:
        return 'NETWORK'

    # azure-core raises ClientAuthenticationError with no status code when a
    # credential cannot be obtained at all - no token, so nothing was sent.
    name = type(exc).__name__
    if 'Authentication' in name or 'Credential' in name:
        return 'AUTH'
    return 'NETWORK'


@register
class CosmosPumphouseConnector(HealthConnector):
    """Read-only adapter over the ``pumphouse_readings`` Cosmos container."""

    key = 'cosmos_pumphouse'

    def __init__(self, source):
        """Bind to a source; no client is built until one is needed."""
        super().__init__(source)
        self._container = None

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @property
    def config(self) -> dict:
        """Non-secret connector settings from the source."""
        return self.source.config or {}

    @property
    def endpoint(self) -> str:
        """Account endpoint URI, or empty when unconfigured."""
        return str(self.config.get('endpoint') or '').strip()

    @property
    def stations(self) -> list[str]:
        """Station uuids this source covers."""
        configured = self.config.get('stations') or []
        if isinstance(configured, str):
            configured = [configured]
        return [str(station).strip() for station in configured if str(station).strip()]

    @property
    def station(self) -> str:
        """The single station this source reads.

        A reading's external key is a JSON pointer such as ``/sl``: it says which
        measurement, not which station. Two stations behind one source would
        therefore write to the same bindings and each would overwrite the other.
        Rather than pick a winner, this refuses - one source, one station.
        """
        stations = self.stations
        if len(stations) == 1:
            return stations[0]
        if not stations:
            raise CosmosConfigError('No station is configured for this source.')
        raise CosmosConfigError(
            'This source lists several stations, but an external key such as '
            "'/sl' does not say which station it came from, so their readings "
            'would overwrite one another. Configure one source per station.'
        )

    @property
    def partition_key_mode(self) -> str:
        """``hierarchical`` (default) or ``composite``."""
        return str(self.config.get('partition_key_mode') or 'hierarchical').lower()

    def _partition_key(self, station: str, hour_bucket) -> object:
        """Build the full partition key for one station-hour.

        Refuses to produce a partial key. With ``enable_cross_partition_query``
        off, a missing component would not silently widen the query - it would
        fail somewhere deeper and less clearly - but the check belongs here,
        before a request is built, because "read one hour of one station" is the
        only shape of request this connector is allowed to make.
        """
        station = str(station or '').strip()
        bucket = str(hour_bucket or '').strip()
        if not station or not bucket:
            raise CosmosConfigError(
                'A query needs both partition key components (station and hour '
                'bucket); refusing to issue a partial-key query.'
            )

        if self.partition_key_mode == 'composite':
            separator = str(self.config.get('partition_key_separator') or '|')
            return f'{station}{separator}{bucket}'
        return [station, bucket]

    # ------------------------------------------------------------------
    # Client
    # ------------------------------------------------------------------

    def _credential(self):
        """Resolve a credential at call time; never store one on the source.

        Entra ID is the only credential accepted against a real account (D6): the
        Data Reader role assignment is what makes read-only an Azure guarantee
        rather than a promise made by this file. An account key is permitted only
        against the local emulator, and only from the environment variable named
        by ``secret_ref`` - so a key can never be read out of the database, an API
        response or a log.
        """
        ref = (self.source.secret_ref or '').strip()
        if not ref:
            from azure.identity import DefaultAzureCredential

            return DefaultAzureCredential()

        if not self._is_emulator():
            raise CosmosConfigError(
                'A key credential may only be used against the local emulator. '
                'Against a real account the connector authenticates with Entra '
                'ID so that the Data Reader role, not this code, enforces '
                'read-only access.'
            )

        key = os.environ.get(ref)
        if not key:
            raise CosmosConfigError(
                f'Environment variable {ref!r} holds no emulator key.'
            )
        return key

    def _is_emulator(self) -> bool:
        """Whether the configured endpoint is the local emulator."""
        from urllib.parse import urlsplit

        host = (urlsplit(self.endpoint).hostname or '').lower()
        return host in EMULATOR_HOSTS

    def container(self):
        """Return the container client, building it once per connector."""
        if self._container is not None:
            return self._container

        endpoint = self.endpoint
        database = str(self.config.get('database') or '').strip()
        container = str(self.config.get('readings_container') or '').strip()
        missing = [
            name
            for name, value in (
                ('endpoint', endpoint),
                ('database', database),
                ('readings_container', container),
            )
            if not value
        ]
        if missing:
            raise CosmosConfigError(f'Source config is missing: {", ".join(missing)}.')

        from azure.cosmos import CosmosClient

        client = CosmosClient(endpoint, credential=self._credential())
        self._container = client.get_database_client(database).get_container_client(
            container
        )
        return self._container

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def check(self) -> tuple[bool, str]:
        """Probe the container, returning ``(ok, code)`` and nothing more.

        Reading the container's own properties is the smallest request that still
        proves all four of endpoint, credential, database and container - and it
        touches no documents, so a probe cannot be turned into a data read.
        """
        try:
            self.container().read()
        except CosmosConfigError:
            return False, 'CONFIG'
        except Exception as exc:
            code = _classify(exc)
            logger.warning(
                'machine_health.cosmos check failed source=%s code=%s',
                self.source.pk,
                code,
            )
            return False, code
        return True, 'OK'

    def _query(self, query: str, parameters: list[dict], station, hour_bucket):
        """Run one single-partition, parameterised query.

        Values only ever reach the service as parameters. Building this string by
        interpolation would put a station uuid from the database straight into
        the query text.
        """
        partition_key = self._partition_key(station, hour_bucket)
        return self.container().query_items(
            query=query,
            parameters=parameters,
            partition_key=partition_key,
            max_item_count=PAGE_SIZE,
            enable_cross_partition_query=False,
        )

    def latest_document(self, station: str, hour_bucket) -> dict | None:
        """Newest snapshot in one station-hour, or None when the hour is empty."""
        rows = self._query(
            QUERY_LATEST,
            [
                {'name': '@station', 'value': station},
                {'name': '@bucket', 'value': str(hour_bucket)},
            ],
            station,
            hour_bucket,
        )
        for row in rows:
            return row
        return None

    def documents_in_bucket(self, station: str, hour_bucket, from_ts, to_ts):
        """Yield snapshots in one bucket within ``[from_ts, to_ts)``, oldest first."""
        yield from self._query(
            QUERY_SLICE,
            [
                {'name': '@station', 'value': station},
                {'name': '@bucket', 'value': str(hour_bucket)},
                {'name': '@from_ts', 'value': int(from_ts)},
                {'name': '@to_ts', 'value': int(to_ts)},
            ],
            station,
            hour_bucket,
        )

    def read_latest(self, external_keys=None) -> list[Reading]:
        """Return the current value for each requested key.

        The current hour is tried first, then the previous one. The fallback is
        not cosmetic: for the first seconds of an hour the current bucket is
        empty, and with hand-seeded data it may stay empty for much longer. What
        it never does is invent a timestamp - a value recovered from the previous
        bucket keeps the observation time the source gave it, so a stale reading
        is visibly stale.
        """
        station = self.station
        now_ms = to_epoch_ms(datetime.now(tz=timezone.utc))

        document = None
        for bucket in (bucket_of(now_ms), bucket_of(now_ms) - HOUR_MS):
            document = self.latest_document(station, bucket)
            if document is not None:
                break

        if document is None:
            return []

        readings = flatten_snapshot(document)
        wanted = {str(key) for key in external_keys} if external_keys else None
        if wanted is None:
            return readings
        return [reading for reading in readings if reading.external_key in wanted]

    def read_window(self, external_key: str, start, end, *, max_samples=None):
        """Return bounded historical samples for one key.

        Timestamps are never rounded to a display interval and gaps are never
        filled: a trend drawn from this shows where the source actually had data.
        """
        start, end, samples = bounded_window(start, end, max_samples=max_samples)
        station = self.station
        start_ms, end_ms = to_epoch_ms(start), to_epoch_ms(end)

        collected: list[Reading] = []
        for bucket in self._buckets(start_ms, end_ms, MAX_BUCKETS_PER_READ):
            window_from = max(start_ms, bucket)
            window_to = min(end_ms, bucket + HOUR_MS)
            for document in self.documents_in_bucket(
                station, bucket, window_from, window_to
            ):
                for reading in flatten_snapshot(document):
                    if reading.external_key != external_key:
                        continue
                    collected.append(reading)
                    if len(collected) >= samples:
                        return collected
        return collected

    @staticmethod
    def _buckets(start_ms: int, end_ms: int, limit: int):
        """Yield each hour bucket touched by ``[start_ms, end_ms)``, bounded."""
        bucket = bucket_of(start_ms)
        count = 0
        while bucket < end_ms and count < limit:
            yield bucket
            bucket += HOUR_MS
            count += 1

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def poll(self, checkpoint, *, now=None, max_documents=None):
        """Yield ``(document, readings)`` from just after the checkpoint onward.

        Strictly after: ``sub_time_period`` is the last sample already accepted,
        so re-reading it would re-present data the application has taken. This
        reads only - the caller decides what to do with a failure, which is what
        lets :meth:`ingest` keep the checkpoint honest.
        """
        station = checkpoint.station_uuid
        now_ms = to_epoch_ms(now or datetime.now(tz=timezone.utc))
        from_ts = int(checkpoint.sub_time_period) + 1
        cap = int(max_documents or self.config.get('max_docs_per_poll') or 200)
        produced = 0

        for bucket in self._buckets(from_ts, now_ms + 1, MAX_BUCKETS_PER_POLL):
            window_from = max(from_ts, bucket)
            for document in self.documents_in_bucket(
                station, bucket, window_from, bucket + HOUR_MS
            ):
                yield document, flatten_snapshot(document)
                produced += 1
                if produced >= cap:
                    return

    def ingest(self, checkpoint, *, now=None, max_documents=None):
        """Read forward from the checkpoint and apply what is read.

        The checkpoint moves once per snapshot, only after every batch of that
        snapshot has been accepted. Advancing per batch would be the subtle
        version of the same bug the ingestion limit already forces us to confront:
        a snapshot is ~760 readings and a batch is 500, so a position saved after
        the first batch would mark a sample as read while a third of its tags had
        never arrived - and, the checkpoint being forward-only, they never would.

        A failure stops the run at the last fully applied snapshot. The next poll
        resumes from there and re-reads the failed one in full.

        Returns ``(documents_applied, readings_applied)``.
        """
        from machine_health.services.ingestion import ingest_readings

        documents = 0
        readings_applied = 0
        snapshots = self.poll(checkpoint, now=now, max_documents=max_documents)

        while True:
            # Reading and applying are stopped by the same rule but fail for
            # different reasons, so they are attempted separately: a snapshot we
            # could not read and one we could not apply both leave the checkpoint
            # where it was.
            try:
                document, readings = next(snapshots)
            except StopIteration:
                break
            except Exception as exc:
                self._stopped(checkpoint, exc)
                break

            try:
                applied = 0
                for batch in in_batches(readings):
                    # `now` is deliberately not forwarded. Here it is the source
                    # clock - how far forward to read - while ingestion's `now` is
                    # the server clock it measures skew against. Passing one as the
                    # other makes every historical sample look like a clock fault.
                    result = ingest_readings(
                        self.source, [reading.as_dict() for reading in batch]
                    )
                    applied += result.accepted
            except Exception as exc:
                self._stopped(checkpoint, exc)
                break

            checkpoint.advance_to(
                document.get('hour_bucket'), int(document['sub_time_period'])
            )
            documents += 1
            readings_applied += applied

        return documents, readings_applied

    def _stopped(self, checkpoint, exc: Exception) -> None:
        """Log a halted run as a code, leaving the checkpoint untouched."""
        code = 'SNAPSHOT' if isinstance(exc, SnapshotError) else _classify(exc)
        logger.warning(
            'machine_health.cosmos ingest stopped source=%s station=%s at=%s code=%s',
            self.source.pk,
            checkpoint.station_uuid,
            checkpoint.sub_time_period,
            code,
        )
