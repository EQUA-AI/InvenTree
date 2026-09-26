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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from django.db import transaction

from machine_health.connectors.base import (
    MAX_TREND_SAMPLES,
    HealthConnector,
    Reading,
    SampledWindow,
    bounded_window,
    register,
    slot_edges,
)
from machine_health.connectors.pumphouse_payload import (
    SnapshotError,
    flatten_snapshot,
    in_batches,
)
from machine_health.services.display_time import MINIMUM_SHIFT

logger = logging.getLogger('inventree')

#: One hour of source samples, in milliseconds. Buckets are half-open:
#: ``hour_bucket <= sub_time_period < hour_bucket + HOUR_MS``.
HOUR_MS = 3_600_000

#: Per-request ceiling when a source does not configure one. Unchanged from the
#: value this connector has always used, so nothing moves without being asked.
DEFAULT_REQUEST_SECONDS = 5.0

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
#: at six hours by ``bounded_window``, which can straddle at most seven hour
#: boundaries; this bounds the number of round trips that window can turn into.
MAX_BUCKETS_PER_READ = 7

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

#: The oldest snapshot in a range: what a sampled read asks for, once per slot.
#: ``TOP 1`` is what makes sampling cheap - the account returns one 50 KB
#: document per slot instead of the seven hundred an hour of them holds.
QUERY_FIRST_IN_RANGE = (
    'SELECT TOP 1 * FROM c '
    'WHERE c.station_uuid = @station AND c.hour_bucket = @bucket '
    'AND c.sub_time_period >= @from_ts AND c.sub_time_period < @to_ts '
    'ORDER BY c.sub_time_period ASC'
)

#: Concurrent slot reads in one sampled window. Each is a single-document query
#: that costs a few RU and mostly waits on the network, so running several at
#: once turns a few hundred sequential round trips into a few seconds. Bounded
#: so a page cannot open hundreds of connections to the account at once.
SAMPLE_WORKERS = 8


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
        # Sampled reads run several queries at once; the RU tally they share
        # must not lose increments to a race.
        self._charge_lock = threading.Lock()

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
            connection_timeout=self.request_seconds,
            read_timeout=self.request_seconds,
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
        with self._charge_lock:
            self.request_charge = (self.request_charge or 0.0) + charge

    @property
    def request_seconds(self) -> float:
        """How long one request to the account may take.

        Five seconds suits an application deployed beside its Cosmos account,
        and is wrong for one reaching it across the internet: measured from a
        developer machine a single partition query to a remote account takes
        three to nine seconds, so a five second cap fails about half of them -
        not on volume, since the same queries are charged only ten to thirteen
        RU, but on round-trip latency alone. Configurable so a distant
        deployment can say so, and defaulted to the original value so a nearby
        one is unaffected.
        """
        configured = (self.config or {}).get('request_timeout_seconds')
        try:
            seconds = float(configured)
        except (TypeError, ValueError):
            return DEFAULT_REQUEST_SECONDS
        return seconds if seconds > 0 else DEFAULT_REQUEST_SECONDS

    def request_timeout(self):
        """Bound each request by the remaining station and sweep budget."""
        allowed = self.request_seconds
        remaining = (
            allowed if self.deadline is None else self.deadline - time.monotonic()
        )
        if remaining <= 0:
            raise PollBudgetError
        return min(allowed, remaining)

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

    def first_document_in_range(
        self, station: str, hour_bucket, from_ts, to_ts
    ) -> dict | None:
        """Oldest snapshot in one bucket within ``[from_ts, to_ts)``, or None."""
        rows = self._query(
            QUERY_FIRST_IN_RANGE,
            [
                {'name': '@station', 'value': station},
                {'name': '@bucket', 'value': str(hour_bucket)},
                {'name': '@from_ts', 'value': int(from_ts)},
                {'name': '@to_ts', 'value': int(to_ts)},
            ],
            station,
            hour_bucket,
        )
        for row in rows:
            return row
        return None

    def sample_windows(self, external_keys, start, end, *, slots: int) -> SampledWindow:
        """Return one snapshot per slot, fetched as one document each.

        A full read of a window costs every snapshot in it - seven hundred
        documents an hour, fifty kilobytes each - which is why trends stop at six
        hours and pages wait tens of seconds for one. This asks the account for
        the first snapshot in each of ``slots`` equal parts of the window, and
        nothing else, so a day costs a few hundred small queries whatever its
        length. The queries run concurrently because each one is mostly a round
        trip.

        Every reading returned is a snapshot the plant actually wrote, carrying
        its own timestamp; nothing is averaged. A slot with no snapshot in it is
        simply absent, so the chart shows the gap. Since every key in a snapshot
        shares its instant, the readings for different keys line up slot for
        slot, which is what lets one chart draw several of them on one axis.
        """
        keys = {str(key) for key in external_keys}
        if not keys or slots < 1:
            return SampledWindow({key: [] for key in keys}, documents_read=0, slots=0)
        if end <= start:
            raise ValueError('Trend window end must not precede its start')

        station = self.station
        edges = [
            (to_epoch_ms(slot_start), to_epoch_ms(slot_end))
            for slot_start, slot_end in slot_edges(start, end, slots)
        ]
        # Build the client on this thread, before any worker needs it, so the
        # credential and connection setup happen once and not eight times.
        self.container()

        def fetch(edge):
            from_ms, to_ms = edge
            # A slot that straddles an hour boundary spans two partitions. The
            # earlier one is asked first; only if it holds nothing is the later
            # one asked, so the reading is still the first in the slot.
            bucket = bucket_of(from_ms)
            while bucket < to_ms:
                document = self.first_document_in_range(
                    station, bucket, max(from_ms, bucket), min(to_ms, bucket + HOUR_MS)
                )
                if document is not None:
                    return document
                bucket += HOUR_MS
            return None

        workers = min(SAMPLE_WORKERS, len(edges))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            documents = list(pool.map(fetch, edges))

        # Slots are half-open and disjoint, and each query is bounded by its
        # own slot, so no snapshot can be picked twice; the documents come back
        # in slot order, which is time order.
        collected: dict[str, list[Reading]] = {key: [] for key in keys}
        fetched = 0
        for document in documents:
            if document is None:
                continue
            fetched += 1
            for reading in flatten_snapshot(document):
                if reading.external_key in keys:
                    collected[reading.external_key].append(reading)

        return SampledWindow(collected, documents_read=fetched, slots=len(edges))

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
        start, end, samples = bounded_window(
            start, end, max_samples=max_samples, ceiling=MAX_TREND_SAMPLES + 1
        )
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

    def read_windows(self, external_keys, start, end, *, max_samples=None):
        """Return samples for many tags from a single pass over the documents.

        A snapshot is a whole-station document holding every tag, and
        ``flatten_snapshot`` parses all of them. Reading one tag at a time means
        fetching and parsing each document once per tag - for a pump page of
        thirty-odd sparklines that is thirty identical scans, and it is the
        largest avoidable cost this adapter has. Here every requested key is
        collected as each document goes past, so the window is read once.

        Returns:
            A mapping of external key to its readings, oldest first; a key with
            no data in the window maps to an empty list.
        """
        wanted = {str(key) for key in external_keys}
        if not wanted:
            return {}

        start, end, samples = bounded_window(
            start, end, max_samples=max_samples, ceiling=MAX_TREND_SAMPLES + 1
        )
        station = self.station
        start_ms, end_ms = to_epoch_ms(start), to_epoch_ms(end)

        collected: dict[str, list[Reading]] = {key: [] for key in wanted}
        # Stop only when *every* key has filled, not when the first one has:
        # tags do not all report at the same cadence.
        outstanding = set(wanted)

        for bucket in self._buckets(start_ms, end_ms, MAX_BUCKETS_PER_READ):
            window_from = max(start_ms, bucket)
            window_to = min(end_ms, bucket + HOUR_MS)
            for document in self.documents_in_bucket(
                station, bucket, window_from, window_to
            ):
                for reading in flatten_snapshot(document):
                    key = reading.external_key
                    if key not in outstanding:
                        continue
                    collected[key].append(reading)
                    if len(collected[key]) >= samples:
                        outstanding.discard(key)
                if not outstanding:
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

    def read_ceiling(self, station: str, now_ms: int) -> int:
        """The newest instant this station may be read up to.

        Normally the wall clock. For a station whose history is a *recorded
        window* - one ``discover_data_range`` has probed, and old enough that the
        dashboard is shifting its times forward to read as live - it is the end
        of that window instead.

        The two must agree. The display offset is derived from the recorded end,
        so a document later than that end is dated *past* the present the moment
        it is shown. Worse, it then wins permanently: ingestion drops an older
        observation as a replay, so nothing that follows can replace it, and the
        station's tiles stay in the future until someone deletes the rows by
        hand. Stations nobody has probed, and windows recent enough that no shift
        is applied, keep reading to the wall clock as before.
        """
        entry = (self.config.get('data_ranges') or {}).get(str(station)) or {}
        stamp = entry.get('to')
        if not stamp:
            return now_ms
        try:
            recorded = datetime.fromisoformat(str(stamp))
        except (TypeError, ValueError):
            return now_ms
        if recorded.tzinfo is None:
            recorded = recorded.replace(tzinfo=timezone.utc)
        recorded_ms = to_epoch_ms(recorded)
        if now_ms - recorded_ms < MINIMUM_SHIFT.total_seconds() * 1000:
            return now_ms
        return min(now_ms, recorded_ms)

    def poll(self, checkpoint, *, now=None, max_documents=None, on_scanned=None):
        """Yield ``(document, readings)`` from just after the checkpoint onward.

        Strictly after: ``sub_time_period`` is the last sample already accepted,
        so re-reading it would re-present data the application has taken. This
        reads only - the caller decides what to do with a failure, which is what
        lets :meth:`ingest` keep the checkpoint honest.

        Reading stops at :meth:`read_ceiling`, not at the wall clock, so the
        poller cannot outrun the window the dashboard anchors to.
        """
        station = checkpoint.station_uuid
        now_ms = to_epoch_ms(now or datetime.now(tz=timezone.utc))
        ceiling_ms = self.read_ceiling(station, now_ms)
        from_ts = max(
            int(checkpoint.sub_time_period) + 1,
            (getattr(checkpoint, 'scan_until', None) or 0) - POLL_LOOKBACK_MS,
        )
        cap = int(max_documents or self.config.get('max_docs_per_poll') or 200)
        produced = 0

        for bucket in self._buckets(from_ts, ceiling_ms + 1, MAX_BUCKETS_PER_POLL):
            self.request_timeout()
            window_from = max(from_ts, bucket)
            window_to = min(ceiling_ms + 1, bucket + HOUR_MS)
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
