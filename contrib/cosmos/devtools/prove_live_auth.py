"""Verify the connector authenticates to live Cosmos with its own credential.

This differs from ``prove_live_read.py`` in the one way that now matters: it
injects **nothing**. The connector resolves its own credential through
``DefaultAzureCredential``, which is what the dashboard will do. If this passes,
authentication is no longer the blocker.

Read-only. Requires AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET.
"""

from datetime import timedelta

from django.utils import timezone

from assets.health_models import HealthSource
from machine_health.connectors.cosmos_pumphouse import CosmosPumphouseConnector

LIVE_ENDPOINT = 'https://epconchatcosmos9d6b.documents.azure.com:443/'
STATION = 'bafc976f-1ccc-4a91-aaa6-c3eac2470d36'

# Built in memory, deliberately not saved. Persisting the live endpoint before
# this check passes would leave the dashboard pointing somewhere it may not be
# able to authenticate.
source = HealthSource.objects.get(pk=1)
source.secret_ref = ''
source.config = dict(source.config, endpoint=LIVE_ENDPOINT)

connector = CosmosPumphouseConnector(source, station_uuid=STATION)

print('endpoint:', LIVE_ENDPOINT)
print('secret_ref:', repr(source.secret_ref), '(empty => Entra ID)')

ok, detail = connector.check()
print(f'check():  ok={ok} detail={detail!r}')

readings = connector.read_latest()
print(f'read_latest(): {len(readings)} readings')
for reading in readings[:3]:
    print(f'    {reading.external_key:<42} = {reading.value!r}')

end = timezone.now()
start = end - timedelta(hours=6)
samples = connector.read_window(
    '/dex/COMMAN_FORBAY_LEVEL', start, end, max_samples=4320
)
print(f'read_window(6h): {len(samples)} samples')
if samples:
    print(f'    first: {samples[0].observed_at} = {samples[0].value!r}')
    print(f'    last:  {samples[-1].observed_at} = {samples[-1].value!r}')

    age = (timezone.now() - samples[-1].observed_at).total_seconds()
    print(f'    newest sample is {age:.0f}s old')

# Writes must fail even with a working credential: the role is Data Reader.
print('\nverifying the role really is read-only...')
try:
    connector.container().upsert_item({
        'id': '__write_probe__',
        'station_uuid': STATION,
        'hour_bucket': '0',
    })
except Exception as exc:
    print(f'    write rejected, as intended: {type(exc).__name__}')
else:
    print('    WARNING: write SUCCEEDED - the identity has more than Data Reader')
