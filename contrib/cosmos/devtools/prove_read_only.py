"""Prove the configured Cosmos identity can read but cannot write (decision D17).

The design relies on Azure, not our code, keeping the connector read-only.
This checks it for whatever identity ``DefaultAzureCredential`` resolves in the
container it runs in (AZURE_CLIENT_ID/SECRET/TENANT_ID, or a managed identity):

1. reads the container properties and one document id;
2. deletes an id that does not exist. A reader gets 403 Forbidden, because RBAC
   is checked before the lookup; a writer would get 404 Not Found. Nothing is
   changed either way;
3. tries to read the sibling containers, which the role should not cover.

Run with plain Python in the app container (no Django needed)::

    docker exec -i <container> python - < contrib/cosmos/devtools/prove_read_only.py

``COSMOS_ENDPOINT``, ``COSMOS_DATABASE``, ``COSMOS_CONTAINER`` and
``COSMOS_OTHER_CONTAINERS`` (comma-separated) override the defaults.
Exits 1 if the identity can write or cannot read.
"""

import os
import sys

from azure.cosmos import CosmosClient, exceptions
from azure.identity import DefaultAzureCredential

ENDPOINT = os.environ.get(
    'COSMOS_ENDPOINT', 'https://epconchatcosmos9d6b.documents.azure.com:443/'
)
DATABASE = os.environ.get('COSMOS_DATABASE', 'aimms')
CONTAINER = os.environ.get('COSMOS_CONTAINER', 'pumphouse_readings')
OTHERS = [
    name
    for name in os.environ.get(
        'COSMOS_OTHER_CONTAINERS', 'telemetry,conversations'
    ).split(',')
    if name
]

credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
credential.get_token('https://cosmos.azure.com/.default')
print(
    'identity: AZURE_CLIENT_ID prefix',
    os.environ.get('AZURE_CLIENT_ID', '')[:8] or '(none: managed identity / CLI)',
)

database = CosmosClient(ENDPOINT, credential=credential).get_database_client(DATABASE)
container = database.get_container_client(CONTAINER)
ok = True

try:
    paths = container.read()['partitionKey']['paths']
    rows = list(
        container.query_items(
            'SELECT TOP 1 c.id FROM c',
            enable_cross_partition_query=True,
            max_item_count=1,
        )
    )
    print(f'READ   {CONTAINER}: ok (partition key {paths}, sample rows {len(rows)})')
except exceptions.CosmosHttpResponseError as exc:
    print(f'READ   {CONTAINER}: FAILED HTTP {exc.status_code}')
    sys.exit(1)

probe_key = ['d17-probe'] * len(paths)
try:
    container.delete_item('d17-write-probe-does-not-exist', partition_key=probe_key)
    print('WRITE  delete succeeded: identity CAN write')
    ok = False
except exceptions.CosmosHttpResponseError as exc:
    if exc.status_code == 403:
        print('WRITE  refused by RBAC (HTTP 403): read-only enforced by Azure')
    elif exc.status_code == 404:
        print('WRITE  NOT refused (HTTP 404 = allowed to try): identity CAN write')
        ok = False
    else:
        print(f'WRITE  inconclusive: HTTP {exc.status_code}')
        ok = False

for name in OTHERS:
    try:
        database.get_container_client(name).read()
        print(f'SCOPE  {name}: readable (role is wider than {CONTAINER})')
    except exceptions.CosmosHttpResponseError as exc:
        print(f'SCOPE  {name}: HTTP {exc.status_code} (outside the role)')

print('RESULT', 'read-only as required' if ok else 'NOT read-only')
sys.exit(0 if ok else 1)
