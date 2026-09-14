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
import time
from datetime import datetime, timezone

from django.db import transaction

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
ERROR_CODES = frozenset({
    'OK',
    'AUTH',
    'NOT_FOUND',
    'THROTTLED',
    'NETWORK',
    'CONFIG',
    'SNAPSHOT',
    'INGEST',
})

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

# Revisit recently scanned empty ranges for delayed documents. Older arrivals
# need an explicit backfill; this connector maintains latest state, not history.
POLL_LOOKBACK_MS = 300_000

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


class PollBudgetError(Exception):
    """Stop at a resumable boundary when the worker's time slice expires."""


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
    if isinstance(exc, CosmosConfigError):
        return 'CONFIG'
    if isinstance(exc, SnapshotError):
        return 'SNAPSHOT'
    from machine_health.services.ingestion import IngestionError

    if isinstance(exc, IngestionError):
        return 'INGEST'
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

    def __init__(self, source, *, station_uuid=None, deadline=None):
        """Bind to a source; no client is built until one is needed."""
        super().__init__(source)
        self._container = None
        self._client = None
        self._identity = None
        self._station_uuid = station_uuid
        self.deadline = deadline
        self.last_error_code = ''
        self.request_charge: float | None = None
        self._charge_missing = False

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
        if self._station_uuid is not None:
            if self._station_uuid not in stations:
                raise CosmosConfigError('Station is not configured for this source.')
            return self._station_uuid
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

            self._identity = DefaultAzureCredential(
                process_timeout=5, connection_timeout=5, read_timeout=5, retry_total=0
            )
            return self._identity

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

        client = CosmosClient(
            endpoint,
            credential=self._credential(),
            timeout=self.request_timeout(),
            connection_timeout=5,
            read_timeout=5,
            retry_total=0,
        )
        self._client = client
        self._container = client.get_database_client(database).get_container_client(
            container
        )
        return self._container

    def close(self):
        """Release HTTP sessions and credential transports after a scheduled poll."""
        try:
            if self._client is not None:
                self._client.close()
        finally:
            if self._identity is not None:
                self._identity.close()

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
        self.request_timeout()
        container = self.container()
        timeout = self.request_timeout()
        return container.query_items(
            query=query,
            parameters=parameters,
            partition_key=partition_key,
            max_item_count=PAGE_SIZE,
            enable_cross_partition_query=False,
            timeout=timeout,
            response_hook=self._record_charge,
        )

    def _record_charge(self, headers, response):
        """Accumulate query RU charges without retaining headers or response bodies."""
        if self._charge_missing:
            return
        try:
            charge = float(headers.get('x-ms-request-charge'))
            if not 0 <= charge < float('inf'):
                raise ValueError('Invalid request charge.')
        except (TypeError, ValueError):
            self._charge_missing = True
            self.request_charge = None
            return
        self.request_charge = (self.request_charge or 0.0) + charge

    def request_timeout(self):
        """Bound each request by the remaining station and sweep budget."""
        remaining = 5.0 if self.deadline is None else self.deadline - time.monotonic()
        if remaining <= 0:
            raise PollBudgetError
        return min(5.0, remaining)

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

    def poll(self, checkpoint, *, now=None, max_documents=None, on_scanned=None):
        """Yield ``(document, readings)`` from just after the checkpoint onward.

        Strictly after: ``sub_time_period`` is the last sample already accepted,
        so re-reading it would re-present data the application has taken. This
        reads only - the caller decides what to do with a failure, which is what
        lets :meth:`ingest` keep the checkpoint honest.
        """
        station = checkpoint.station_uuid
        now_ms = to_epoch_ms(now or datetime.now(tz=timezone.utc))
        from_ts = max(
            int(checkpoint.sub_time_period) + 1,
            (getattr(checkpoint, 'scan_until', None) or 0) - POLL_LOOKBACK_MS,
        )
        cap = int(max_documents or self.config.get('max_docs_per_poll') or 200)
        produced = 0

        for bucket in self._buckets(from_ts, now_ms + 1, MAX_BUCKETS_PER_POLL):
            self.request_timeout()
            window_from = max(from_ts, bucket)
            window_to = min(now_ms + 1, bucket + HOUR_MS)
            for document in self.documents_in_bucket(
                station, bucket, window_from, window_to
            ):
                self.request_timeout()
                if str(document.get('station_uuid')) != str(station):
                    raise SnapshotError('Snapshot belongs to another station.')
                yield document, flatten_snapshot(document)
                produced += 1
                if produced >= cap:
                    return
            if on_scanned is not None:
                on_scanned(window_to)

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
        self.last_error_code = ''
        if (
            checkpoint.source_id != self.source.pk
            or not checkpoint.active
            or checkpoint.station_uuid not in self.stations
            or not checkpoint.station_id
            or checkpoint.station.asset_type != 'pumphouse'
            or not checkpoint.station.active
            or (
                self.source.client_id is not None
                and self.source.client_id != checkpoint.station.client_id
            )
            or str(checkpoint.station.source_entity_uuid) != checkpoint.station_uuid
        ):
            raise CosmosConfigError(
                'Checkpoint requires matching registered station ownership.'
            )

        def scanned(until):
            if until > (checkpoint.scan_until or 0):
                checkpoint.scan_until = until
                checkpoint.save(update_fields=['scan_until', 'updated_at'])

        documents = 0
        readings_applied = 0
        snapshots = self.poll(
            checkpoint, now=now, max_documents=max_documents, on_scanned=scanned
        )

        while True:
            # Reading and applying are stopped by the same rule but fail for
            # different reasons, so they are attempted separately: a snapshot we
            # could not read and one we could not apply both leave the checkpoint
            # where it was.
            try:
                document, readings = next(snapshots)
            except StopIteration:
                break
            except PollBudgetError:
                break
            except Exception as exc:
                self._stopped(checkpoint, exc)
                break

            try:
                applied = self._apply_snapshot(checkpoint, document, readings)
            except PollBudgetError:
                break
            except Exception as exc:
                self._stopped(checkpoint, exc)
                break

            documents += 1
            readings_applied += applied

        return documents, readings_applied

    @transaction.atomic
    def _apply_snapshot(self, checkpoint, document, readings):
        """Commit every batch and its accepted position together, or none of them."""
        from machine_health.services.ingestion import ingest_readings

        applied = 0
        for batch in in_batches(readings):
            self.request_timeout()
            # The source read horizon is not the server clock for future skew.
            result = ingest_readings(
                self.source,
                [reading.as_dict() for reading in batch],
                station=checkpoint.station,
            )
            if result.rejected:
                raise SnapshotError('Snapshot contains rejected readings.')
            applied += result.accepted
        checkpoint.advance_to(
            document.get('hour_bucket'), int(document['sub_time_period'])
        )
        return applied

    def _stopped(self, checkpoint, exc: Exception) -> None:
        """Log a halted run as a code, leaving the checkpoint untouched."""
        code = _classify(exc)
        self.last_error_code = code
        logger.warning(
            'machine_health.cosmos ingest stopped source=%s station=%s at=%s code=%s',
            self.source.pk,
            checkpoint.station_uuid,
            checkpoint.sub_time_period,
            code,
        )
