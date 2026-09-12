#!/usr/bin/env python3
"""Build and upsert pumphouse snapshot documents into Cosmos.

Seeding is how this sprint gets data: the Cassandra migration is deferred, so a
small number of hand-authored snapshots stand in for the live feed. That makes
the document-building rules load-bearing, and they live here rather than in the
connector so that both the seeder and the reader agree on one shape.

Two invariants are enforced on every document, because a violation is cheap to
introduce and expensive to notice later:

* the sample must sit inside its own hour bucket, half-open at the top, and
* ``month`` must be the bucket's month in **UTC**, matching the legacy
  ``iwm_data_YYYYMM`` table suffix.

``data1_raw`` carries the payload exactly as the source would store it, and
``payload_hash`` covers that text. Parsed fields alongside it are a convenience
for indexing and queries; the raw text is the record of truth, and anything the
parsed fields claim must be re-derivable from it.

Run with --dry-run to print exactly what would be written. Writing needs a
data-plane role (Cosmos DB Data Contributor); the control-plane role that can
create a container deliberately cannot write a document.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SAMPLES = Path(__file__).parent / 'samples' / 'ph3_snapshots.json'
HOUR_MS = 3_600_000
EMULATOR_ENDPOINT = 'https://localhost:8081'
EMULATOR_KEY_ENV = 'COSMOS_EMULATOR_KEY'


class SeedError(Exception):
    """A snapshot that must not be written as it stands."""


def month_of(hour_bucket: str) -> str:
    """Return the UTC ``YYYYMM`` a bucket belongs to.

    UTC is not a detail: the source's monthly tables are suffixed this way, and
    deriving the month in local time would file the first hours of a month under
    the previous one for anyone east of Greenwich.
    """
    moment = datetime.fromtimestamp(int(hour_bucket) / 1000, tz=timezone.utc)
    return moment.strftime('%Y%m')


def build_document(snapshot: dict, station_uuid: str, selectors: dict) -> dict:
    """Turn one snapshot into the document the connector expects to read back."""
    try:
        bucket = int(snapshot['time_period'])
        sample = int(snapshot['sub_time_period'])
    except (KeyError, TypeError, ValueError) as exc:
        raise SeedError(f'time_period and sub_time_period must be epoch ms: {exc}')

    if not bucket <= sample < bucket + HOUR_MS:
        raise SeedError(
            f'sample {sample} is outside its hour bucket {bucket}; '
            'a timestamp at the next boundary belongs to the next bucket'
        )

    payload = snapshot['data1']
    if not isinstance(payload, dict):
        raise SeedError('data1 must be an object in the sample file.')

    # Separators without spaces, keys in source order: the raw text is what the
    # hash covers, so it has to be produced the same way every run.
    raw = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)

    document = {
        'id': str(sample),
        'station_uuid': station_uuid,
        'hour_bucket': str(bucket),
        'sub_time_period': sample,
        'month': month_of(str(bucket)),
        # Per D13 the station is its own parent in this feed; the seeder writes
        # all three identifiers from one value rather than inviting a mismatch.
        'parent_entity_uuid': snapshot.get('parent_entity_uuid', station_uuid),
        'entity_uuid': station_uuid,
        **selectors,
        **{key: value for key, value in payload.items() if key not in {'pd', 'dex'}},
        'pd': payload.get('pd', {}),
        'dex': payload.get('dex', {}),
        'data2': None,
        'data1_raw': raw,
        'payload_hash': 'sha256:' + hashlib.sha256(raw.encode('utf-8')).hexdigest(),
        'ingested_at': datetime.now(tz=timezone.utc).isoformat(),
    }

    verify(document)
    return document


def verify(document: dict) -> None:
    """Re-check a built document the way a reader will, before it is written."""
    bucket = int(document['hour_bucket'])
    if not bucket <= document['sub_time_period'] < bucket + HOUR_MS:
        raise SeedError('Built document escaped its hour bucket.')

    if document['month'] != month_of(document['hour_bucket']):
        raise SeedError('Built document month does not match its bucket in UTC.')

    reparsed = json.loads(document['data1_raw'])
    for key, value in reparsed.items():
        if document.get(key) != value:
            raise SeedError(
                f'Parsed field {key!r} disagrees with data1_raw. The raw payload '
                'is authoritative, so this document would misrepresent it.'
            )


def load_snapshots(path: Path, station_uuid: str | None) -> tuple[str, dict, list]:
    """Read the sample file and resolve the station identity to write."""
    sample_file = json.loads(path.read_text(encoding='utf-8'))
    station = station_uuid or sample_file['station_uuid']
    selectors = sample_file.get('selectors', {})
    snapshots = [
        dict(
            snapshot, parent_entity_uuid=sample_file.get('parent_entity_uuid', station)
        )
        for snapshot in sample_file['snapshots']
    ]
    return station, selectors, snapshots


def ladder(snapshot: dict, count: int, every_ms: int) -> list[dict]:
    """Repeat one snapshot forward in time, re-bucketing as hours roll over.

    Useful for producing enough samples to exercise paging without hand-writing
    them. Values are unchanged and deliberately so: this makes timestamps, not
    plausible process data, and pretending otherwise would put invented readings
    where real ones belong.
    """
    produced = []
    sample = int(snapshot['sub_time_period'])
    for index in range(count):
        moment = sample + index * every_ms
        bucket = moment - (moment % HOUR_MS)
        payload = dict(snapshot['data1'], egt=moment, ext=moment + 300_000)
        produced.append({
            'time_period': str(bucket),
            'sub_time_period': moment,
            'data1': payload,
        })
    return produced


def client_for(args):
    """Build a Cosmos client, preferring Entra ID over a key."""
    from azure.cosmos import CosmosClient

    if args.emulator:
        key = os.environ.get(EMULATOR_KEY_ENV)
        if not key:
            sys.exit(f'Set {EMULATOR_KEY_ENV} to the emulator key.')
        return CosmosClient(args.endpoint or EMULATOR_ENDPOINT, credential=key)

    from azure.identity import DefaultAzureCredential

    if not args.endpoint:
        sys.exit('Pass --endpoint, or set INVENTREE_COSMOS_ENDPOINT.')
    return CosmosClient(args.endpoint, credential=DefaultAzureCredential())


def build_parser() -> argparse.ArgumentParser:
    """Command-line surface; --dry-run writes nothing."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--samples', type=Path, default=SAMPLES)
    parser.add_argument('--station-uuid', help='Overrides the sample file')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--count', type=int, help='Repeat the first snapshot N times')
    parser.add_argument('--every-ms', type=int, default=5000)
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
    """Build every document, then write them only if asked to."""
    args = build_parser().parse_args(argv)
    station, selectors, snapshots = load_snapshots(args.samples, args.station_uuid)

    if args.count:
        snapshots = ladder(snapshots[0], args.count, args.every_ms)

    try:
        documents = [build_document(s, station, selectors) for s in snapshots]
    except SeedError as exc:
        sys.exit(f'Refusing to seed: {exc}')

    buckets = sorted({document['hour_bucket'] for document in documents})
    print(f'{len(documents)} document(s) for station {station}')
    print(f'hour buckets: {", ".join(buckets)}')

    if args.dry_run:
        print(json.dumps(documents, indent=2)[:4000])
        print('\n--dry-run: nothing written.')
        return 0

    container = (
        client_for(args)
        .get_database_client(args.database)
        .get_container_client(args.container)
    )
    for document in documents:
        try:
            container.upsert_item(document)
        except Exception as exc:
            sys.exit(
                f'Write failed ({type(exc).__name__}). Seeding needs the Cosmos DB '
                'Data Contributor data-plane role; the control-plane role that '
                'creates containers cannot write documents. See README.md.'
            )
    print(
        f'Upserted {len(documents)} document(s) into {args.database}/{args.container}.'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
