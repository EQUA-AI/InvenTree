"""Copy readings from the local emulator into a live Cosmos container.

Why this needs to exist separately from seed.py: seed.py writes the small
hand-authored fixture, which carries its real July-2025 timestamps. What the
dashboard needs in order to draw anything is a *recent* series, and the only
recent series that exists anywhere is the one the dev reseed loop has
accumulated in the emulator. This moves that across.

What it will not do quietly
---------------------------

These documents are not plant history. They are one snapshot - captured, per
BLOCKERS.md Ask 1, while the station was **shut down** - replayed onto later
timestamps by the freshness loop. Written into a shared account with no
marking, they would be indistinguishable from real telemetry to the next
person who queries the container, and they would read as a pump that ran for
days at standstill values.

So every document this writes carries ``synthetic: true`` and a note saying
where it came from. The fields are additive, which the schema explicitly
permits (plan.md:490 - "Only additive fields allowed"), and the indexing
policy does not index them, so the cost is a few bytes and no RU.

It also refuses to write unless ``--confirm`` is passed. A dry run is the
default because the target is shared infrastructure and this is the one
operation here that cannot be undone by restarting a container.

Deleting them afterwards is possible - ``--purge-synthetic`` removes exactly
the documents this wrote, matched on the marker, and nothing else.

Auth: the dev container has the SDK but no Azure CLI, so mint a token on the
host and pass it in via COSMOS_ACCESS_TOKEN. Writing needs the Cosmos DB
Built-in Data Contributor role (``...0002``); Data Reader (``...0001``) is
not enough and will fail with a 403.

Run from the repository root. Local development tooling.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

EMULATOR_ENDPOINT = 'http://localhost:8081'
EMULATOR_KEY_ENV = 'COSMOS_EMULATOR_KEY'
TOKEN_ENV = 'COSMOS_ACCESS_TOKEN'
TOKEN_EXPIRES_ENV = 'COSMOS_ACCESS_TOKEN_EXPIRES'

#: Marker written onto every document this tool copies.
#:
#: Named as a field rather than encoded in the id, because ids are the
#: partition-local key the connector reads back by and must keep matching
#: sub_time_period. A field can also be queried, which is what makes the
#: purge exact rather than a guess at a prefix.
SYNTHETIC_FLAG = 'synthetic'
SYNTHETIC_NOTE = (
    'Replayed dev fixture, not plant history. One PH_3 snapshot captured with '
    'the station shut down, rebased onto later timestamps by the emulator '
    'freshness loop. Do not read as telemetry.'
)


def live_client(endpoint: str):
    """Return a client for a live account, preferring an injected token."""
    from azure.cosmos import CosmosClient

    token = os.environ.get(TOKEN_ENV)
    if token:
        from azure.core.credentials import AccessToken, TokenCredential

        expires = int(os.environ.get(TOKEN_EXPIRES_ENV, '0'))

        class StaticToken(TokenCredential):
            """Hand the SDK a token the caller already obtained."""

            def get_token(self, *scopes, **kwargs):
                return AccessToken(token, expires)

        return CosmosClient(endpoint, credential=StaticToken())

    from azure.identity import DefaultAzureCredential

    return CosmosClient(endpoint, credential=DefaultAzureCredential())


def emulator_client(endpoint: str):
    """Return a client for the local emulator."""
    from azure.cosmos import CosmosClient

    key = os.environ.get(EMULATOR_KEY_ENV)
    if not key:
        sys.exit(f'Set {EMULATOR_KEY_ENV} to read the emulator.')
    return CosmosClient(endpoint, credential=key)


def mark(document: dict) -> dict:
    """Stamp a document as synthetic and strip Cosmos' own metadata.

    The ``_``-prefixed system fields belong to the source container's copy of
    the document - carrying ``_etag`` across would attach a stale concurrency
    token to a brand new write.
    """
    cleaned = {k: v for k, v in document.items() if not k.startswith('_')}
    cleaned[SYNTHETIC_FLAG] = True
    cleaned['synthetic_note'] = SYNTHETIC_NOTE
    cleaned['synthetic_copied_at'] = datetime.now(tz=timezone.utc).isoformat()
    return cleaned


def select(container, since_ms: int | None, limit: int | None) -> list[dict]:
    """Read the documents to copy, newest last."""
    query = 'SELECT * FROM c'
    params: list[dict] = []
    if since_ms is not None:
        query += ' WHERE c.sub_time_period >= @since'
        params.append({'name': '@since', 'value': since_ms})
    query += ' ORDER BY c.sub_time_period ASC'

    docs = list(
        container.query_items(
            query, parameters=params or None, enable_cross_partition_query=True
        )
    )
    if limit is not None and len(docs) > limit:
        # Keep the newest, not the oldest. A trend chart reads backwards from
        # now, so truncating the recent end would leave exactly the part
        # nobody can see.
        docs = docs[-limit:]
    return docs


def purge(container, confirm: bool) -> int:
    """Delete the documents this tool wrote, and only those."""
    docs = list(
        container.query_items(
            f'SELECT c.id, c.station_uuid, c.hour_bucket FROM c '
            f'WHERE c.{SYNTHETIC_FLAG} = true',
            enable_cross_partition_query=True,
        )
    )
    print(f'{len(docs)} synthetic documents found')
    if not confirm:
        print('dry run: pass --confirm to delete them')
        return 0

    for doc in docs:
        container.delete_item(
            doc['id'], partition_key=[doc['station_uuid'], doc['hour_bucket']]
        )
    print(f'deleted {len(docs)}')
    return len(docs)


def main() -> None:
    """Copy emulator documents to a live container, or purge what was copied."""
    parser = argparse.ArgumentParser(
        description='Copy emulator readings into a live Cosmos container.'
    )
    parser.add_argument('--endpoint', required=True, help='Live account endpoint')
    parser.add_argument('--database', default='aimms')
    parser.add_argument('--container', default='pumphouse_readings')
    parser.add_argument(
        '--source-endpoint',
        default=os.environ.get('INVENTREE_COSMOS_ENDPOINT', EMULATOR_ENDPOINT),
    )
    parser.add_argument(
        '--hours',
        type=float,
        default=6.0,
        help='Copy samples from the last N hours (default 6, matching the '
        'longest window the trend chart can read)',
    )
    parser.add_argument('--limit', type=int, default=None, help='Cap documents copied')
    parser.add_argument(
        '--confirm',
        action='store_true',
        help='Actually write. Without this, nothing is sent.',
    )
    parser.add_argument(
        '--purge-synthetic',
        action='store_true',
        help='Delete previously copied synthetic documents and exit',
    )
    args = parser.parse_args()

    target = (
        live_client(args.endpoint)
        .get_database_client(args.database)
        .get_container_client(args.container)
    )

    if args.purge_synthetic:
        purge(target, args.confirm)
        return

    source = (
        emulator_client(args.source_endpoint)
        .get_database_client(args.database)
        .get_container_client(args.container)
    )

    since_ms = None
    if args.hours:
        now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        since_ms = now_ms - int(args.hours * 3_600_000)

    docs = select(source, since_ms, args.limit)
    print(f'source:  {args.source_endpoint}')
    print(f'target:  {args.endpoint} {args.database}/{args.container}')
    print(f'selected {len(docs)} documents')

    if not docs:
        print('nothing to copy')
        return

    times = [int(d['sub_time_period']) for d in docs]
    span = (max(times) - min(times)) / 1000
    print(
        f'span:    {datetime.fromtimestamp(min(times) / 1000, tz=timezone.utc)}'
        f' .. {datetime.fromtimestamp(max(times) / 1000, tz=timezone.utc)}'
        f'  ({span / 3600:.2f}h)'
    )
    print(f'every document will carry {SYNTHETIC_FLAG}=true')

    if not args.confirm:
        print('\nDRY RUN - nothing written. Re-run with --confirm to write.')
        return

    written = 0
    for doc in docs:
        target.upsert_item(mark(doc))
        written += 1
        if written % 25 == 0:
            print(f'  {written}/{len(docs)}')
    print(f'wrote {written} documents')


if __name__ == '__main__':
    main()
