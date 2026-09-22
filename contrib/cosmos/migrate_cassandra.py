"""Migrate pumphouse telemetry from Cassandra into Cosmos, one bounded run at a time.

This is the job that plan.md decision D2 deferred. It is deliberately a
standalone tool rather than part of the Django application:

- `cassandra-driver` is a heavy native dependency. plan.md rejected the Cassandra
  API for the connector partly on those grounds, and a one-directional backfill
  does not justify putting the driver in the production image. Install it from
  `contrib/cosmos/requirements-migrate.txt` in whatever environment runs this.
- The application's Cosmos connector has, by design, no write path at all -
  `upsert_item` and friends appear nowhere in it, so that the Data Reader role
  rather than a code review enforces read-only. Writing belongs outside it.

It reuses, rather than reimplements, the two pieces that already exist:

- `seed.build_document()` is already the Cassandra-row -> Cosmos-document
  mapping. It takes `{time_period, sub_time_period, data1}`, which is exactly a
  Cassandra row, and produces the envelope the connector reads back, including
  the hour-bucket invariant, the deterministic `data1_raw`, its `payload_hash`
  and a reader-style `verify()`. plan.md forbids a second normaliser and this
  respects that: if the mapping is wrong, it is wrong in one place.
- `seed.month_of()` decides which monthly table an hour belongs to.

What this file adds is the two things that genuinely do not exist: a CQL reader,
and a rate-capped resumable writer.

Shape of the source, confirmed against the checked-in PH_3 excerpt
------------------------------------------------------------------

    PRIMARY KEY ((parent_entity_uuid, location_type, component_type,
                  time_period, event_value_type), sub_time_period, entity_uuid)

`time_period` is **in the partition key**, and it is `text` holding epoch
milliseconds. Two consequences drive this file's structure:

1. An hour is a whole partition, so there is no such thing as a range scan over
   time. Hours are enumerated explicitly, one bounded single-partition query
   each. That is also why progress is recorded per hour.
2. `time_period` must be bound as a **string**. Binding an int silently matches
   nothing rather than erroring - the same text-versus-int trap already recorded
   against `assets/registry.py`.

`sub_time_period` is the first clustering column, so `> ?` plus `ORDER BY` is
allowed inside one partition, which is what makes a partly-migrated hour
resumable without re-reading it.

Cost, and why this self-throttles
---------------------------------

Measured on 2026-09-19: one document is 97,243 B and costs **96.76 RU** to
write, and the `aimms` database has **400 RU/s shared across the whole
database** - the minimum. That is about four documents a second, and the live
dashboard reads from that same pool. A backfill running flat out would starve
it, and the symptom would be every reading reporting stale: indistinguishable
from a dead connector.

So the writer takes a share of the pool and stays under it, measuring the RU
the server actually charged rather than trusting the 96.76 estimate. Default
cap is 150 RU/s, leaving the majority for reads.

Idempotency
-----------

A document's `id` is its sample time, so writes are upserts of a stable key.
Re-running a completed range rewrites identical documents and changes nothing.
That is what makes interrupting this safe.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seed import SeedError, build_document, month_of

HOUR_MS = 3_600_000

# The registered pumphouse's coordinates already live here, so they are read
# rather than retyped: a mistyped station_uuid writes correct data into a
# partition the connector never reads, which is a silent loss, not an error.
SAMPLES = Path(__file__).resolve().parent / 'samples' / 'ph3_snapshots.json'

# Well under the 400 RU/s the database shares with live dashboard reads.
DEFAULT_RU_CAP = 150.0

# Used only until the server tells us what a write really cost.
ESTIMATED_RU_PER_DOC = 96.76


class MigrationError(Exception):
    """A refusal to migrate, stated rather than worked around."""


# --------------------------------------------------------------------------
# Source coordinates
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Partition:
    """The four partition-key columns that are constant across a migration.

    The fifth, `time_period`, varies per hour and is passed separately - it is
    what makes each hour a different partition.
    """

    parent_entity_uuid: str
    location_type: str
    component_type: int
    event_value_type: int


def identities_from_sample(path: Path) -> tuple[str, str, dict]:
    """Read the registered station's identity and selectors from a sample file.

    Returns `(station_uuid, parent_entity_uuid, selectors)`.

    The distinction that matters: `entity_uuid` is the station itself and
    becomes the Cosmos partition key, while `parent_entity_uuid` is the
    Cassandra partition the station's rows sit under. They are different values
    in this feed, so neither can be derived from the other.
    """
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise MigrationError(f'Cannot read {path}: {exc}') from None

    station = raw.get('station_uuid')
    parent = raw.get('parent_entity_uuid', station)
    selectors = raw.get('selectors')

    if not station:
        raise MigrationError(f'{path} carries no station_uuid.')
    if not isinstance(selectors, dict) or not selectors:
        raise MigrationError(f'{path} carries no selectors.')

    missing = {'location_type', 'component_type', 'event_value_type'} - set(selectors)
    if missing:
        raise MigrationError(f'{path} selectors are missing {sorted(missing)}.')

    return str(station), str(parent), dict(selectors)


ESTATE = Path(__file__).resolve().parent.parent / 'pump-cassandra' / 'estate.json'


def load_estate(path: Path = ESTATE) -> dict:
    """Return the estate inventory keyed by both source name and uuid.

    Every station is reachable by either, so a run can name the plant's own
    station - `--station 'Saraswati PH'` - rather than pasting a uuid. A
    mistyped uuid is the worst input this tool takes: it writes correct data
    into a partition the connector never reads, which is a silent loss.
    """
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise MigrationError(f'Cannot read the estate inventory: {exc}') from None

    index: dict[str, dict] = {}
    for station in raw.get('stations', []):
        uuid = station.get('source_uuid')
        if not uuid:
            continue
        index[uuid.lower()] = station
        for name in (station.get('source_name'), station.get('display_name')):
            if name:
                index[name.strip().lower()] = station
    if not index:
        raise MigrationError(f'{path} lists no stations.')
    return index


def resolve_station(token: str, estate: dict | None = None) -> dict:
    """Look one station up by uuid, plant name, or display name."""
    estate = estate if estate is not None else load_estate()
    found = estate.get(str(token).strip().lower())
    if found is None:
        names = sorted({s['source_name'] for s in estate.values()})
        raise MigrationError(f'Unknown station {token!r}. Known: ' + ', '.join(names))
    return found


def table_for(hour_bucket_ms: int, prefix: str = 'iwm_data_') -> str:
    """Return the monthly table an hour belongs to, in UTC.

    `month_of` is reused so that the table name and the document's own `month`
    field can never disagree about which month an hour is in.
    """
    return f'{prefix}{month_of(str(hour_bucket_ms))}'


# How the source derives `time_period` from a sample time. This is not a
# preference - it is part of the partition key, so getting it wrong returns zero
# rows rather than wrong rows, which reads as "there is no data".
#
#   hour    bucket = floor(sample, 1h);          bucket covers [B, B+1h)
#   lagged  bucket = floor(sample, 30m) - 30m;   bucket covers [B+30m, B+1h)
#
# `hour` is the default because it is what the checked-in data does, measured
# rather than assumed: across 3,000 partitions of `iwm_data_202507` all 729
# distinct `time_period` values are hour-aligned, none at :30, in all five
# (location_type, event_value_type) combinations. And within one bucket both
# halves of the hour are populated - 1,231 samples in [B, B+30m) and 1,186 in
# [B+30m, B+1h) - which only the `hour` rule allows.
#
# `lagged` is supported because a feed may well use it; under it a sample at
# 08:29 belongs to bucket 07:30. Do not guess between them: a wrong choice is
# silent.
BUCKET_MODES = ('hour', 'lagged')
HALF_MS = 1_800_000


def source_buckets(start_ms: int, end_ms: int, mode: str = 'hour') -> list[int]:
    """Every source partition that can hold a sample inside [start, end).

    The end is exclusive, matching the connector's `scan_until`, so a migration
    and a poll describe a range the same way.

    **One partition past the end is included, deliberately.** The source files a
    sample at `hh:59:59.999` under the *next* hour, so the last sample of the
    requested range physically lives in the partition after it. Reading only
    `[start, end)` left every range short by exactly that row - measured against
    the live account, hour 23:00 of a full day had 720 of 721 documents because
    `23:59:59.999` sits in the 07-03 00:00 partition, which the range excluded.

    Callers must therefore ignore rows from that extra partition whose own
    derived bucket falls outside the range; `migrate()` does this. Reading one
    partition too many and discarding is cheap. Missing the boundary row is not
    detectable without reconciling against the source.
    """
    if end_ms <= start_ms:
        raise MigrationError('The end of the range must be after its start.')
    if mode not in BUCKET_MODES:
        raise MigrationError(f'Bucket mode must be one of {BUCKET_MODES}.')

    if mode == 'hour':
        first = (start_ms // HOUR_MS) * HOUR_MS
        # `end_ms + HOUR_MS` so the partition after the range is read too: it
        # holds the range's own final `hh:59:59.999` sample.
        return list(range(first, end_ms + HOUR_MS, HOUR_MS))

    # Under `lagged`, bucket B covers [B+30m, B+1h), so the bucket holding
    # `start` is the one whose window contains it, and buckets step by 30m.
    first = (start_ms // HALF_MS) * HALF_MS - HALF_MS
    return list(range(first, end_ms, HALF_MS))


def bucket_for_sample(sample_ms: int, mode: str = 'hour') -> int:
    """The source bucket a sample would be filed under, by the given rule."""
    if mode == 'lagged':
        return (sample_ms // HALF_MS) * HALF_MS - HALF_MS
    return (sample_ms // HOUR_MS) * HOUR_MS


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------


class RuBudget:
    """Keep sustained request-unit spend under a ceiling.

    A plain token bucket, with the refill rate as the ceiling. It exists because
    the destination database's throughput is shared with the live dashboard: the
    point is not to make the migration fast but to make it invisible to
    everything else using those RU.

    `clock` and `sleeper` are injected so tests can assert the pacing without
    spending real time.
    """

    def __init__(self, ru_per_second: float, clock=time.monotonic, sleeper=time.sleep):
        """Fill the bucket at ``ru_per_second``, against an injectable clock."""
        if ru_per_second <= 0:
            raise MigrationError('The RU cap must be positive.')
        self.ru_per_second = float(ru_per_second)
        self._clock = clock
        self._sleeper = sleeper
        self._allowance = float(ru_per_second)
        self._last = clock()
        self.total_ru = 0.0
        self.total_waited = 0.0

    def spend(self, ru: float) -> None:
        """Account for `ru` already charged, waiting first if it exceeds the cap."""
        now = self._clock()
        self._allowance = min(
            self.ru_per_second,
            self._allowance + (now - self._last) * self.ru_per_second,
        )
        self._last = now

        if ru > self._allowance:
            deficit = ru - self._allowance
            delay = deficit / self.ru_per_second
            self._sleeper(delay)
            self.total_waited += delay
            self._allowance += delay * self.ru_per_second
            self._last = self._clock()

        self._allowance -= ru
        self.total_ru += ru


# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------


@dataclass
class Progress:
    """What has been fully migrated, so an interrupted run can resume.

    Two separate facts, for the same reason the connector keeps them apart:

    - `completed_hours` are hours read to exhaustion and fully written. Only
      these may be skipped outright.
    - `partial` records the last sample written inside an hour that was cut
      short by a limit or an error. Resuming reads that hour again from after
      that sample rather than from its start.

    An hour that was interrupted is never recorded as completed, so the failure
    mode of this file is redundant work, not a silent gap.
    """

    completed_hours: set[int] = field(default_factory=set)
    partial: dict[int, int] = field(default_factory=dict)

    # Which destination this progress describes. Progress is only meaningful
    # against the container it was recorded for: a file from an emulator run,
    # reused against a live account, would mark every hour already done and
    # write nothing at all - a silent no-op that reads as a clean success.
    target: str = ''

    @classmethod
    def load(cls, path: Path | None, target: str = '') -> Progress:
        """Read progress, treating an absent or unreadable file as a fresh start.

        Refuses a file recorded against a different destination rather than
        silently skipping work.
        """
        if path is None or not path.exists():
            return cls(target=target)
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise MigrationError(f'Progress file is unreadable: {exc}') from None

        recorded = str(raw.get('target') or '')
        if target and recorded and recorded != target:
            raise MigrationError(
                f'{path} records progress against {recorded!r}, but this run '
                f'targets {target!r}. Reusing it would mark hours already done '
                'and write nothing. Use a separate progress file per destination.'
            )
        return cls(
            completed_hours={int(h) for h in raw.get('completed_hours', [])},
            partial={int(k): int(v) for k, v in raw.get('partial', {}).items()},
            target=recorded or target,
        )

    def save(self, path: Path | None) -> None:
        """Persist progress. Written after every hour, not at the end."""
        if path is None:
            return
        path.write_text(
            json.dumps(
                {
                    'target': self.target,
                    'completed_hours': sorted(self.completed_hours),
                    'partial': {str(k): v for k, v in sorted(self.partial.items())},
                },
                indent=2,
            )
        )

    def resume_after(self, hour: int) -> int | None:
        """The sample to read after within `hour`, or None to read it whole."""
        return self.partial.get(hour)

    def finish(self, hour: int) -> None:
        """Mark an hour fully migrated."""
        self.completed_hours.add(hour)
        self.partial.pop(hour, None)

    def interrupt(self, hour: int, last_sample: int) -> None:
        """Record how far an hour got without claiming it is done."""
        self.partial[hour] = last_sample


# --------------------------------------------------------------------------
# Cassandra
# --------------------------------------------------------------------------


class CassandraReader:
    """Bounded single-partition reads of one hour at a time.

    Takes a live `session` rather than building one, so the migration logic is
    testable without a cluster and so connection policy stays in one place
    (`session_from_env`).
    """

    def __init__(self, session, table_prefix: str = 'iwm_data_'):
        """Read through a live Cassandra session, one month table per hour."""
        self.session = session
        self.table_prefix = table_prefix
        self._statements: dict[tuple[str, bool], object] = {}

    def _statement(self, table: str, resuming: bool):
        """Prepare per (table, shape) and cache it; preparing every hour is waste."""
        key = (table, resuming)
        if key not in self._statements:
            resume_clause = ' AND sub_time_period > ?' if resuming else ''
            self._statements[key] = self.session.prepare(
                f'SELECT entity_uuid, sub_time_period, time_period, data1 '
                f'FROM {table} '
                f'WHERE parent_entity_uuid = ? AND location_type = ? '
                f'AND component_type = ? AND time_period = ? '
                f'AND event_value_type = ?{resume_clause} '
                f'ORDER BY sub_time_period ASC'
            )
        return self._statements[key]

    def rows_for_hour(
        self, partition: Partition, hour_bucket: int, after: int | None = None
    ):
        """Yield rows of one hour in sample order, oldest first.

        Two adjacent partition-key columns need opposite treatment, and both
        are confirmed from `system_schema.columns`:

        - `time_period` is **text** holding epoch milliseconds, so it is bound
          as a string. Binding an int matches zero rows *without raising*,
          which is indistinguishable from an empty hour - the same text/int
          trap already recorded against `assets/registry.py`.
        - `parent_entity_uuid` is a real **uuid**, so it must be a `UUID`
          object. A string raises `TypeError` at bind time, which at least
          fails loudly rather than silently.
        """
        table = table_for(hour_bucket, self.table_prefix)
        values = [
            _as_uuid(partition.parent_entity_uuid),
            partition.location_type,
            partition.component_type,
            str(hour_bucket),
            partition.event_value_type,
        ]
        if after is not None:
            values.append(after)

        for row in self.session.execute(
            self._statement(table, after is not None), values
        ):
            yield _row_to_dict(row)


def _as_uuid(value):
    """Coerce a uuid-typed bind parameter, accepting either form.

    Callers and the sample file carry UUIDs as strings, which is the right
    shape for a Cosmos document. The driver needs the real type. Converting
    here keeps that distinction at the one boundary where it matters.
    """
    from uuid import UUID

    return value if isinstance(value, UUID) else UUID(str(value))


def _row_to_dict(row) -> dict:
    """Normalise a driver row into a plain mapping.

    The driver yields namedtuple-like rows; tests pass dicts. Accepting both
    keeps the reader honest without a driver import.
    """
    if isinstance(row, dict):
        return dict(row)
    return {
        'entity_uuid': row.entity_uuid,
        'sub_time_period': row.sub_time_period,
        'time_period': row.time_period,
        'data1': row.data1,
    }


def session_from_env():
    """Connect using the environment, never arguments.

    Credentials are read from the process environment so they can live in
    `contrib/container/docker.dev.secrets.env` (gitignored via `*.env`, mode
    0600) instead of a command line, where they would reach shell history and
    the process table.
    """
    from cassandra.auth import PlainTextAuthProvider
    from cassandra.cluster import Cluster
    from cassandra.policies import DCAwareRoundRobinPolicy, TokenAwarePolicy

    keyspace = os.environ.get('CASSANDRA_KEYSPACE')
    if not keyspace:
        raise MigrationError('Set CASSANDRA_KEYSPACE.')

    contact_points = [
        point.strip()
        for point in os.environ.get('CASSANDRA_CONTACT_POINTS', '127.0.0.1').split(',')
        if point.strip()
    ]
    user = os.environ.get('CASSANDRA_USERNAME')
    password = os.environ.get('CASSANDRA_PASSWORD')
    local_dc = os.environ.get('CASSANDRA_LOCAL_DC', 'datacenter1')

    # Token-aware over DC-aware: a single-partition read then goes straight to a
    # replica that owns it, rather than through a coordinator hop per hour.
    cluster = Cluster(
        contact_points=contact_points,
        port=int(os.environ.get('CASSANDRA_PORT', '9042')),
        auth_provider=(
            PlainTextAuthProvider(username=user, password=password) if user else None
        ),
        load_balancing_policy=TokenAwarePolicy(
            DCAwareRoundRobinPolicy(local_dc=local_dc)
        ),
        protocol_version=int(os.environ.get('CASSANDRA_PROTOCOL_VERSION', '4')),
    )
    return cluster.connect(keyspace)


# --------------------------------------------------------------------------
# Migration
# --------------------------------------------------------------------------


@dataclass
class Outcome:
    """What a run actually did, for printing and for tests to assert on."""

    documents: int = 0
    hours_done: int = 0
    hours_skipped: int = 0
    rows_read: int = 0
    ru: float = 0.0
    waited: float = 0.0
    stopped_early: str = ''
    running_bays: int = 0

    # A shared partition's other tenants. Reported rather than silently
    # dropped: which stations share a parent is how the estate's real shape
    # gets discovered, and D19's inventory is still an open question.
    rows_other_stations: int = 0
    other_stations: set = field(default_factory=set)

    # Rows whose Cosmos bucket had to be recomputed because the source filed
    # them under a different hour. Counted so a re-bucketing stays a reported
    # fact rather than a silent rewrite.
    rebucketed: int = 0

    # Rows read only because the partition after the range had to be opened
    # to find its boundary row. Not errors; not migrated either.
    rows_out_of_range: int = 0

    # Of those, the ones the source's own stated rule did not predict. Under
    # `lagged` a re-bucket is expected everywhere, so this is what separates
    # "working as described" from "the source disagrees with itself".
    unexpected_rebucket: int = 0


def _data1_object(row: dict) -> dict:
    """Parse Cassandra's `data1` text into the object `build_document` expects.

    `build_document` re-serialises this canonically (no spaces, source key
    order) and hashes that form. So the stored `data1_raw` is the canonical
    rendering of the source payload, not its original bytes. That is the
    existing behaviour of every document already in the container, and matching
    it matters more than byte-fidelity: a reader verifies parsed fields against
    `data1_raw`, and two different renderings of the same payload would make
    `payload_hash` depend on who wrote the document.
    """
    payload = row.get('data1')
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, str) or not payload.strip():
        raise MigrationError('Row carries no data1 payload.')
    try:
        parsed = json.loads(payload)
    except ValueError as exc:
        raise MigrationError(f'data1 is not readable JSON: {exc}') from None
    if not isinstance(parsed, dict):
        raise MigrationError('data1 must contain a JSON object.')
    return parsed


def _is_requested_station(row: dict, expected: str) -> bool:
    """Whether this row belongs to the station being migrated.

    One Cassandra partition holds several stations. Confirmed against the real
    data: partition `(dd4b923d…, PUMP_HOUSE, 65, 1751421600000, 41)` carries
    rows for `776913b4…`, `bafc976f…` and `4162f034…` interleaved, because
    `entity_uuid` is a *clustering* column, not part of the partition key. So a
    foreign row is the normal case, not corruption, and must be skipped rather
    than treated as an error.

    It still has to be checked per row. `entity_uuid` is the station's own
    identity and `parent_entity_uuid` is the partition it sits under; the two
    differ, and one value can be both (`bafc976f…` appears as a parent of other
    rows as well as a station under `dd4b923d…`). Writing a row under the wrong
    `station_uuid` would file it in a Cosmos partition the connector never
    reads - a silent loss rather than a failure - so the query's coordinates
    are not taken as proof of a row's identity.
    """
    found = str(row.get('entity_uuid') or '')
    if not found:
        raise MigrationError('Row carries no entity_uuid to identify its station.')
    return found == expected


def _counts_a_running_bay(payload: dict) -> bool:
    """Whether any bay in this payload reports running.

    Tracked because it is the single most valuable thing a real migration can
    surface: every snapshot in the repository so far was captured with the
    station shut down, which is why 264 dictionary points are withheld rather
    than approved. The first running sample is what unblocks those units, so the
    run reports whether it found any instead of leaving someone to go looking.
    """
    if payload.get('st') == 'R':
        return True
    bays = payload.get('pd')
    if isinstance(bays, dict):
        for bay in bays.values():
            if isinstance(bay, dict) and bay.get('st') == 'R':
                return True
    return False


DEFAULT_WRITE_CONCURRENCY = 8


def _write_batch(pending, writer, workers: int):
    """Write a batch of documents concurrently, in submit order.

    `pending` is `[(sample, document), ...]` in ascending sample order.
    Returns `(charges, cursor, failure)` where `cursor` is the highest sample
    whose write, **and every write before it in this batch**, succeeded.

    That contiguous-prefix rule is the whole point. Writes finish out of order,
    so taking the newest success as the resume point could step over a document
    that never landed - and because progress then records it as done, that gap
    would never be revisited. Advancing only through an unbroken run of
    successes makes the failure mode redundant work instead of missing data.

    Documents after a failure may well have been written; they were already in
    flight. Re-running rewrites them, which is free: the document id is the
    sample time, so every write is an idempotent upsert.

    Concurrency does not raise the average write rate - the RU budget still
    caps it - it stops the rate collapsing to one-round-trip-at-a-time. Each
    write costs ~96.76 RU against a 150 RU/s share, so serialised it idles
    waiting on latency rather than on the cap. It does make spend burstier
    inside the cap's averaging window; the SDK's own 429 retry covers that.
    """
    if not pending:
        return [], None, None

    if writer is None:  # dry run: nothing to write
        return [], pending[-1][0], None

    if workers <= 1:
        results = []
        for sample, document in pending:
            try:
                results.append((sample, writer(document), None))
            except Exception as exc:
                results.append((sample, 0.0, exc))
                break
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=workers) as pool:
            # Submit everything first so the batch is genuinely concurrent,
            # then collect in submit order so the prefix rule can be applied.
            futures = [(s, pool.submit(writer, d)) for s, d in pending]
            results = []
            for sample, future in futures:
                try:
                    results.append((sample, future.result(), None))
                except Exception as exc:
                    results.append((sample, 0.0, exc))

    charges: list[float] = []
    cursor = None
    for sample, charge, exc in results:
        if exc is not None:
            return charges, cursor, exc
        charges.append(charge)
        cursor = sample
    return charges, cursor, None


def migrate(
    *,
    reader: CassandraReader,
    partition: Partition,
    station_uuid: str,
    selectors: dict,
    hours: list[int],
    writer=None,
    budget: RuBudget | None = None,
    progress: Progress | None = None,
    progress_path: Path | None = None,
    max_docs: int | None = None,
    max_seconds: float | None = None,
    bucket_mode: str = 'hour',
    sample_range: tuple[int, int] | None = None,
    inline_extension: bool = False,
    write_concurrency: int = DEFAULT_WRITE_CONCURRENCY,
    clock=time.monotonic,
    log=print,
) -> Outcome:
    """Read the given hours from Cassandra and write them to Cosmos.

    `writer` is a callable taking a document and returning the RU it cost, or
    None when nothing is being written (a dry run). Injecting it keeps the
    Cosmos SDK out of this function entirely, so the orchestration is testable
    without an account or an emulator.

    Bounded on purpose: `max_docs` and `max_seconds` exist so a first run can be
    a pilot rather than a commitment, and so an operator can stop a backfill
    without losing the work already done.
    """
    progress = progress if progress is not None else Progress()
    budget = budget or RuBudget(DEFAULT_RU_CAP)
    outcome = Outcome()
    started = clock()

    for hour in hours:
        if hour in progress.completed_hours:
            outcome.hours_skipped += 1
            continue

        after = progress.resume_after(hour)
        if after is not None:
            log(f'  hour {hour}: resuming after sample {after}')

        last_sample = after
        exhausted = True
        pending: list[tuple[int, dict]] = []

        # `buffer` is bound at definition time rather than closed over: this is
        # redefined once per hour, and a closure over the loop variable would
        # make a deferred call write whichever hour's buffer happened to be
        # current. It never is deferred today, but the binding says so.
        def flush(buffer=pending):
            """Write the buffer, charge the budget, advance the cursor safely.

            Returns the failure that stopped the batch, or None. The budget is
            charged from this thread only, so the token bucket needs no locking.
            """
            nonlocal last_sample
            if not buffer:
                return None
            charges, cursor, failure = _write_batch(buffer, writer, write_concurrency)
            for charge in charges:
                budget.spend(charge)
            outcome.documents += len(charges) if writer is not None else len(buffer)
            if cursor is not None:
                last_sample = cursor
            buffer.clear()
            return failure

        for row in reader.rows_for_hour(partition, hour, after):
            outcome.rows_read += 1

            # Skipped before parsing: a foreign row's payload is none of this
            # station's business, and parsing ~700 tags to discard them is the
            # bulk of the work in a partition shared by several stations.
            if not _is_requested_station(row, station_uuid):
                outcome.rows_other_stations += 1
                outcome.other_stations.add(str(row['entity_uuid']))
                # Still advance the cursor: this row has been dealt with, and
                # resuming before it would re-read it forever.
                last_sample = row['sub_time_period']
                continue

            # Discard rows outside the requested sample range. The final
            # partition is read only for its boundary row; its other rows
            # belong to the next hour and were not asked for.
            if sample_range is not None:
                sample = row['sub_time_period']
                if not sample_range[0] <= sample < sample_range[1]:
                    outcome.rows_out_of_range += 1
                    last_sample = sample
                    continue

            payload = _data1_object(row)
            if _counts_a_running_bay(payload):
                outcome.running_bays += 1

            # The Cosmos hour bucket is DERIVED from the sample, not copied
            # from Cassandra's `time_period`.
            #
            # The source does not always agree with itself here. Measured in
            # partition (dd4b923d…, PUMP_HOUSE, 65, 1751421600000, 41): a
            # sample at 1751421599999 - 01:59:59.999, one millisecond before
            # the bucket it is filed under - appears for four stations at once,
            # so it is a convention rather than a stray row. The source assigns
            # a sample on the hour boundary to the hour about to start.
            #
            # Cosmos cannot follow that convention. The connector enumerates
            # hour buckets and issues one single-partition read per bucket,
            # relying on `bucket <= sample < bucket + 1h`. A document filed one
            # bucket ahead of its own sample would sit in a partition no reader
            # queries for that instant: invisible, not merely misplaced. So the
            # bucket is recomputed to the one that actually contains the sample.
            #
            # Nothing is lost. `sub_time_period` carries the exact sample time
            # and `data1_raw` carries the source's own `egt`, so the original
            # instant survives verbatim; only the storage bucket is restated.
            derived_bucket = (row['sub_time_period'] // HOUR_MS) * HOUR_MS
            source_bucket = str(row.get('time_period', hour))
            if str(derived_bucket) != source_bucket:
                outcome.rebucketed += 1
                # Under `lagged` every row is re-bucketed by design, so only
                # note it as a surprise when the source claimed hour buckets.
                if bucket_mode == 'hour':
                    outcome.unexpected_rebucket += 1

            snapshot = {
                'time_period': str(derived_bucket),
                'sub_time_period': row['sub_time_period'],
                'data1': payload,
                'parent_entity_uuid': partition.parent_entity_uuid,
            }
            try:
                document = build_document(
                    snapshot, station_uuid, selectors, inline_extension=inline_extension
                )
            except SeedError as exc:
                # The mapping refused this row. Stopping rather than skipping:
                # a payload the shared builder will not vouch for is a fact
                # about the source that someone needs to see.
                progress.interrupt(hour, last_sample) if last_sample else None
                progress.save(progress_path)
                raise MigrationError(
                    f'Row {row.get("sub_time_period")} in hour {hour}: {exc}'
                ) from None

            pending.append((row['sub_time_period'], document))

            # --max-docs must be exact, not "exact to within a batch". The
            # limit is counted against documents already written PLUS those
            # buffered, because everything buffered will be written; checking
            # only the written count would overshoot by up to one batch.
            reached_limit = (
                max_docs is not None and outcome.documents + len(pending) >= max_docs
            )

            if reached_limit or len(pending) >= write_concurrency:
                failure = flush()
                if failure is not None:
                    progress.interrupt(hour, last_sample) if last_sample else None
                    progress.save(progress_path)
                    raise MigrationError(
                        f'Write failed in hour {hour} '
                        f'({type(failure).__name__}): {failure}'
                    ) from None
                # Checkpoint mid-hour, not just at the hour boundary. A hard
                # kill cannot run cleanup, so anything held only in memory is
                # lost: killing a run mid-hour previously discarded the whole
                # hour's cursor and re-read all ~721 rows on resume. Verified
                # against the live account - 409 documents were rewritten for
                # nothing. Saving per batch caps that redo at one batch.
                #
                # Safe to record as partial even though the hour may yet
                # finish: `finish()` clears the partial entry when it does.
                if last_sample is not None and not reached_limit:
                    progress.interrupt(hour, last_sample)
                    progress.save(progress_path)

            if reached_limit:
                outcome.stopped_early = f'document limit ({max_docs}) reached'
                exhausted = False
                break
            if max_seconds is not None and clock() - started >= max_seconds:
                outcome.stopped_early = f'time limit ({max_seconds}s) reached'
                exhausted = False
                break

        # Whatever is still buffered belongs to this hour and must land before
        # the hour can be called finished.
        failure = flush()
        if failure is not None:
            progress.interrupt(hour, last_sample) if last_sample else None
            progress.save(progress_path)
            raise MigrationError(
                f'Write failed in hour {hour} ({type(failure).__name__}): {failure}'
            ) from None

        if exhausted:
            progress.finish(hour)
            outcome.hours_done += 1
        elif last_sample is not None:
            progress.interrupt(hour, last_sample)

        progress.save(progress_path)

        if outcome.stopped_early:
            break

    outcome.ru = budget.total_ru
    outcome.waited = budget.total_waited
    return outcome


# --------------------------------------------------------------------------
# Cosmos writer
# --------------------------------------------------------------------------


DEFAULT_WRITE_ATTEMPTS = 4


def _is_transient(exc) -> bool:
    """Whether a write failure is worth retrying.

    Read timeouts, connection faults, throttling and the 5xx/408 family are
    transient. Anything else - a rejected document, a permissions problem, a
    bad partition key - will fail identically on retry and should surface.
    """
    name = type(exc).__name__
    if name in {
        'ServiceResponseError',
        'ServiceResponseTimeoutError',
        'ServiceRequestError',
        'ServiceRequestTimeoutError',
        'ConnectionError',
        'ReadTimeout',
        'ReadTimeoutError',
    }:
        return True
    return getattr(exc, 'status_code', None) in {408, 429, 500, 502, 503, 504}


def cosmos_writer(
    container, attempts: int = DEFAULT_WRITE_ATTEMPTS, sleeper=time.sleep
):
    """Return a callable that upserts a document and reports its real RU cost.

    The charge is read from the response header rather than estimated, because
    the whole point of the cap is to stay under a shared ceiling and a stale
    estimate would defeat it. When the header is missing - the emulator omits
    it - the measured production figure stands in, which keeps the cap
    conservative rather than unbounded.
    """

    def write(document: dict) -> float:
        charged: list[float] = []

        def hook(headers, _result=None):
            try:
                charged.append(float(headers.get('x-ms-request-charge', 0.0)))
            except (TypeError, ValueError):
                pass

        # Retry transient failures here rather than letting them end the run.
        #
        # Retrying a write that timed out is normally unsafe: the request may
        # have succeeded, and a retry would duplicate it. That objection does
        # not apply here - the document id IS the sample time, so every write
        # is an upsert of a fixed key and a repeat is a no-op replace.
        #
        # Worth doing because it is not rare. Two consecutive live runs ended
        # on `ServiceResponseTimeoutError` (a 65 s read timeout) against
        # epconchatcosmos9d6b, at write concurrency 8 and again at 4, so it is
        # not a concurrency artefact. A 38-hour station-month would hit it
        # repeatedly; without retry the backfill needs a babysitter.
        last = None
        for attempt in range(1, max(1, attempts) + 1):
            try:
                container.upsert_item(document, response_hook=hook)
                return (
                    charged[0] if charged and charged[0] > 0 else ESTIMATED_RU_PER_DOC
                )
            except Exception as exc:
                last = exc
                if attempt == attempts or not _is_transient(exc):
                    raise
                # Back off, so a struggling account is not hammered harder.
                sleeper(min(2 ** (attempt - 1), 8))
                charged.clear()
        raise last  # unreachable; kept so the contract is explicit

    return write


def _az_available() -> bool:
    """Whether the Azure CLI is on PATH and can therefore mint tokens."""
    from shutil import which

    return which('az') is not None


class _CliCredential:
    """An Entra token that re-mints itself from the Azure CLI before expiring.

    A backfill outlives its credential. An access token lasts about an hour and
    a full station-month is ~74 hours at the default cap, so a credential
    fetched once would fail part-way through - and the SDK's own retry cannot
    fix an expired token. This is the same reasoning `keep_live_fresh.sh`
    records for re-minting every cycle: "a loop that minted once would run fine
    through a demo and then fail silently overnight".

    The subprocess is bounded. `az account get-access-token` was observed
    hanging for 10h11m after a host slept mid-call, holding a dead socket; an
    unbounded call here would wedge the migration in exactly the same way.
    """

    def __init__(self, endpoint: str, margin_seconds: int = 300):
        # The data-plane resource is the account, without port or path.
        host = str(endpoint).split('//', 1)[-1].split('/', 1)[0].split(':', 1)[0]
        self.resource = f'https://{host}'
        self.margin = margin_seconds
        self._token = None
        self._expires = 0.0
        self.mints = 0

    def get_token(self, *_scopes, **_kwargs):
        import subprocess

        from azure.core.credentials import AccessToken

        if self._token and time.time() < self._expires - self.margin:
            return AccessToken(self._token, int(self._expires))

        try:
            done = subprocess.run(
                [
                    'az',
                    'account',
                    'get-access-token',
                    '--resource',
                    self.resource,
                    # Ask for the real expiry, not just the token. `az` serves a
                    # CACHED token that may be almost spent, so assuming
                    # "now + 55 min" claims a lifetime the token does not have.
                    # That is not theoretical: three streams died with "token
                    # has been expired since 17:01:23, current server time is
                    # 17:17:23" because the cached token was ~55 min old when
                    # first fetched, and nothing ever refreshed it.
                    '--query',
                    '{t:accessToken,e:expires_on}',
                    '-o',
                    'json',
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise MigrationError(
                'az account get-access-token did not return within 120s. It has '
                'been seen to block indefinitely on a dead socket after a host '
                'sleeps; re-run and it will resume from --progress.'
            ) from None

        if done.returncode != 0:
            raise MigrationError(
                f'Could not mint an Azure token (exit {done.returncode}). '
                f'Is `az login` still valid? {(done.stderr or "").strip()[:200]}'
            )

        try:
            parsed = json.loads(done.stdout or '{}')
            token = str(parsed.get('t') or '').strip()
            expires = int(parsed.get('e') or 0)
        except (ValueError, TypeError) as exc:
            raise MigrationError(f'Unreadable token response: {exc}') from None

        if not token or not expires:
            raise MigrationError(
                'az returned no token or no expiry. Older CLI versions omit '
                '`expires_on`; upgrade the Azure CLI or export '
                'COSMOS_ACCESS_TOKEN and run where `az` is absent.'
            )

        self._token = token
        self._expires = float(expires)
        self.mints += 1

        remaining = self._expires - time.time()
        if remaining <= self.margin:
            # `az` handed back a token already inside our refresh margin. Using
            # it is correct - it is still valid - but say so, because the next
            # call will immediately ask for another and a silent tight loop
            # would look like a hang.
            print(
                f'NOTE: the token az returned expires in {remaining:.0f}s, '
                f'inside the {self.margin}s refresh margin.'
            )
        return AccessToken(self._token, int(self._expires))


def container_for(args):
    """Open the destination container, preferring a host-minted token.

    `COSMOS_ACCESS_TOKEN` is checked first because it is the path that actually
    works from inside the dev container, which has no Azure CLI and no managed
    identity: the token is minted on the host and passed in. See
    `contrib/cosmos/BLOCKERS.md`.
    """
    from azure.cosmos import CosmosClient

    if args.emulator:
        key = os.environ.get('COSMOS_EMULATOR_KEY')
        if not key:
            raise MigrationError('Set COSMOS_EMULATOR_KEY for --emulator.')
        client = CosmosClient(args.endpoint, credential=key)
    elif _az_available():
        client = CosmosClient(args.endpoint, credential=_CliCredential(args.endpoint))
    else:
        token = os.environ.get('COSMOS_ACCESS_TOKEN')
        if token:
            from azure.core.credentials import AccessToken

            expires = int(
                os.environ.get('COSMOS_ACCESS_TOKEN_EXPIRES', time.time() + 3000)
            )
            if expires - time.time() < 7200:
                print(
                    'WARNING: using a fixed COSMOS_ACCESS_TOKEN that expires in '
                    f'{(expires - time.time()) / 60:.0f} min. A backfill longer '
                    'than that will fail part-way; it resumes from --progress, '
                    'but run it where `az` is available to avoid the interruption.'
                )

            class _Static:
                def get_token(self, *_scopes, **_kwargs):
                    return AccessToken(token, expires)

            client = CosmosClient(args.endpoint, credential=_Static())
        else:
            from azure.identity import DefaultAzureCredential

            client = CosmosClient(args.endpoint, credential=DefaultAzureCredential())

    return client.get_database_client(args.database).get_container_client(
        args.container
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _parse_moment(text: str) -> int:
    """Accept epoch milliseconds or an ISO-8601 instant, and return epoch ms."""
    if text.isdigit():
        return int(text)
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise MigrationError(
            f'Not a time: {text!r}. Use epoch ms or ISO-8601 (2025-07-18T15:00Z).'
        ) from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def build_parser() -> argparse.ArgumentParser:
    """The command line, including the flags that make a run destructive."""
    parser = argparse.ArgumentParser(
        description='Migrate pumphouse telemetry from Cassandra into Cosmos.'
    )
    parser.add_argument(
        '--from-sample',
        type=Path,
        default=SAMPLES,
        help='File supplying the station identity and selectors (default: the '
        'registered PH_3 sample). Every value below defaults to what it says.',
    )
    parser.add_argument(
        '--station',
        help="The station to migrate, by plant name ('Saraswati PH'), display "
        "name ('Millbrook Influent Pump Station') or uuid. Resolved through "
        'contrib/pump-cassandra/estate.json so a uuid cannot be mistyped.',
    )
    parser.add_argument(
        '--station-uuid',
        help="Override the station's entity_uuid, which becomes Cosmos partition "
        'key 1. Prefer --station. Must match a registered station or the '
        'connector will not read what is written.',
    )
    parser.add_argument(
        '--parent-entity-uuid',
        help='Override the Cassandra partition this station sits under. This is '
        'NOT the station identity; the two differ in this feed.',
    )
    parser.add_argument('--location-type')
    parser.add_argument('--component-type', type=int)
    parser.add_argument('--event-value-type', type=int)
    parser.add_argument('--table-prefix', default='iwm_data_')
    parser.add_argument(
        '--inline-dex',
        action='store_true',
        help='Also store the parsed dex tag map alongside data1_raw. Doubles '
        'document size for a copy of bytes the reader already re-parses from '
        'data1_raw, so it is off by default.',
    )
    parser.add_argument(
        '--source-bucket',
        choices=BUCKET_MODES,
        default='hour',
        help="How the SOURCE derives time_period from a sample. 'hour' = "
        'floor to the hour (what the checked-in data measurably does). '
        "'lagged' = floor to 30 min then minus 30 min, so 08:29 sits in "
        '07:30. Wrong choice returns zero rows, not wrong rows.',
    )

    parser.add_argument(
        '--from', dest='start', required=True, help='Epoch ms or ISO-8601. Inclusive.'
    )
    parser.add_argument(
        '--to',
        dest='end',
        help='Epoch ms or ISO-8601. Exclusive. Defaults to one hour after --from.',
    )

    parser.add_argument(
        '--ru-cap',
        type=float,
        default=DEFAULT_RU_CAP,
        help=f'Sustained RU/s ceiling (default {DEFAULT_RU_CAP}). The database '
        'shares 400 RU/s with live dashboard reads.',
    )
    parser.add_argument(
        '--write-concurrency',
        type=int,
        default=DEFAULT_WRITE_CONCURRENCY,
        help=f'Concurrent upserts per batch (default {DEFAULT_WRITE_CONCURRENCY}). '
        'The RU cap still bounds the average rate; this stops it idling on '
        'round-trip latency. 1 writes strictly serially.',
    )
    parser.add_argument('--max-docs', type=int)
    parser.add_argument('--max-seconds', type=float)
    parser.add_argument(
        '--progress',
        type=Path,
        help='Progress file, so an interrupted run resumes instead of restarting.',
    )

    parser.add_argument(
        '--confirm',
        action='store_true',
        help='Actually write. Without it this reads Cassandra and writes nothing.',
    )
    parser.add_argument('--emulator', action='store_true')
    parser.add_argument(
        '--endpoint', default=os.environ.get('INVENTREE_COSMOS_ENDPOINT')
    )
    parser.add_argument(
        '--database', default=os.environ.get('INVENTREE_COSMOS_DATABASE', 'aimms')
    )
    parser.add_argument(
        '--container',
        default=os.environ.get('INVENTREE_COSMOS_CONTAINER', 'pumphouse_readings'),
    )
    return parser


def main(argv=None) -> int:
    """Run one migration and return a process exit status."""
    args = build_parser().parse_args(argv)

    try:
        start = _parse_moment(args.start)
        end = _parse_moment(args.end) if args.end else start + HOUR_MS
        hours = source_buckets(start, end, args.source_bucket)

        sample_station, sample_parent, selectors = identities_from_sample(
            args.from_sample
        )
        station_uuid = args.station_uuid or sample_station
        parent_entity_uuid = args.parent_entity_uuid or sample_parent

        # --station wins over the sample file's default, but never over an
        # explicit --station-uuid: the more specific flag stays authoritative.
        station = None
        if args.station:
            station = resolve_station(args.station)
            station_uuid = args.station_uuid or station['source_uuid']
            if not station.get('present_in_dump', True):
                print(
                    f'NOTE: {station["source_name"]} is in the inventory but was '
                    'not observed in the local dump; expect zero rows.'
                )

        # Overrides are folded into the selectors too, so the values used to
        # query Cassandra and the values stamped onto the document are always
        # the same three. Keeping two copies is how they drift.
        for flag, key in (
            (args.location_type, 'location_type'),
            (args.component_type, 'component_type'),
            (args.event_value_type, 'event_value_type'),
        ):
            if flag is not None:
                selectors[key] = flag

        partition = Partition(
            parent_entity_uuid=parent_entity_uuid,
            location_type=selectors['location_type'],
            component_type=selectors['component_type'],
            event_value_type=selectors['event_value_type'],
        )

        tables = sorted({table_for(hour, args.table_prefix) for hour in hours})
        if station:
            print(
                f'station:         {station["source_name"]} '
                f'-> {station["display_name"]}'
            )
            print(
                f'pumps:           {station.get("pumps_stated")} '
                f'| dex tags: {station.get("dex_tags", "unknown")} '
                f'| feeds: {station.get("feeds")}'
            )
        print(f'identities from: {args.from_sample}')
        print(f'station_uuid:    {station_uuid}   (Cosmos partition key 1)')
        print(f'parent_entity:   {parent_entity_uuid}   (Cassandra partition)')
        print(f'selectors:       {json.dumps(selectors)}')
        print(f'hours:           {len(hours)} ({hours[0]} .. {hours[-1]})')
        print(f'tables:          {", ".join(tables)}')
        print(f'source bucket:   {args.source_bucket}')
        print(f'ru cap:          {args.ru_cap}/s')
        print(f'write conc:      {args.write_concurrency}')

        target = f'{args.endpoint}/{args.database}/{args.container}'
        progress = Progress.load(args.progress, target)
        if progress.completed_hours or progress.partial:
            print(
                f'progress: {len(progress.completed_hours)} hour(s) already done, '
                f'{len(progress.partial)} partial'
            )

        reader = CassandraReader(session_from_env(), args.table_prefix)

        writer = None
        if args.confirm:
            if not args.endpoint:
                raise MigrationError(
                    'Pass --endpoint, or set INVENTREE_COSMOS_ENDPOINT.'
                )
            writer = cosmos_writer(container_for(args))
        else:
            print('\nNo --confirm: reading Cassandra, writing nothing.\n')

        outcome = migrate(
            reader=reader,
            partition=partition,
            station_uuid=station_uuid,
            selectors=selectors,
            hours=hours,
            writer=writer,
            budget=RuBudget(args.ru_cap),
            progress=progress,
            progress_path=args.progress,
            max_docs=args.max_docs,
            max_seconds=args.max_seconds,
            bucket_mode=args.source_bucket,
            write_concurrency=args.write_concurrency,
            sample_range=(start, end),
            inline_extension=args.inline_dex,
        )
    except MigrationError as exc:
        sys.exit(f'Refusing to migrate: {exc}')

    print(
        f'\nrows read:      {outcome.rows_read:,}\n'
        f'  this station: {outcome.rows_read - outcome.rows_other_stations:,}\n'
        f'  other tenants:{outcome.rows_other_stations:,} (skipped)\n'
        f'documents:      {outcome.documents:,}'
        f'{"" if args.confirm else " (not written)"}\n'
        f'hours done:     {outcome.hours_done:,}\n'
        f'hours skipped:  {outcome.hours_skipped:,}\n'
        f'RU spent:       {outcome.ru:,.0f}\n'
        f'throttled for:  {outcome.waited:,.1f}s'
    )
    if outcome.stopped_early:
        print(f'stopped early:  {outcome.stopped_early}')

    if outcome.rebucketed:
        if args.source_bucket == 'lagged':
            print(
                f'\n{outcome.rebucketed:,} row(s) re-bucketed, as expected under '
                "--source-bucket=lagged: the source's bucket lags the\nsample by "
                'up to an hour, and Cosmos stores the real hour taken from '
                'sub_time_period.'
            )
        else:
            print(
                f'\n{outcome.unexpected_rebucket:,} row(s) were re-bucketed: the '
                'source filed them under an hour that does not contain\ntheir own '
                'sample time. The Cosmos bucket is derived from the sample so the '
                'connector can find them.\nSample times themselves are unchanged.'
            )

    if outcome.other_stations:
        # The estate inventory (D19) is still an open question, so what shares
        # this parent is worth stating rather than discarding.
        print(
            f'\nThis partition is shared with {len(outcome.other_stations)} other '
            'station(s), whose rows were skipped:'
        )
        for other in sorted(outcome.other_stations):
            print(f'  {other}')
        print(
            'Each needs registering and migrating separately. Note the read '
            'already returned their rows, so\nmigrating them from the same '
            'sweep would cost no extra Cassandra reads.'
        )

    # Reported prominently because it is what unblocks the withheld units.
    if outcome.running_bays:
        print(
            f'\n{outcome.running_bays:,} sample(s) report a RUNNING bay. '
            'This is what BLOCKERS.md Ask 1 has been waiting for: run\n'
            '  python3 contrib/pump-cassandra/value_ranges.py <dump>\n'
            'to settle the withheld units from observed magnitudes.'
        )
    elif outcome.rows_read:
        print(
            '\nNo sample in this range reports a running bay, so this data '
            'cannot settle the magnitude-dependent units (BLOCKERS.md Ask 1).'
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
