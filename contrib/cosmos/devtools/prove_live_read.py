"""Exercise the real connector against the live account, using an injected token.

The point is to separate two questions that are easy to conflate while waiting
on a role assignment:

  1. Can the application authenticate?   (No - that is the blocker.)
  2. Does everything *after* authentication work against the live container?

Only the first is blocked. If the second were also broken - a partition-key
mismatch, a schema difference, documents the flattener cannot parse - then
granting the role would not produce a working dashboard either, and nobody
would discover that until after the wait.

So this substitutes the credential and changes nothing else. The connector's
own query construction, partition-key rules, paging, parsing and flattening all
run exactly as they would in production, against the documents actually sitting
in the live container.

Read-only. Requires COSMOS_ACCESS_TOKEN.
"""

import os
from datetime import timedelta

from django.utils import timezone

from assets.health_models import HealthSource
from machine_health.connectors.cosmos_pumphouse import CosmosPumphouseConnector

LIVE_ENDPOINT = 'https://epconchatcosmos9d6b.documents.azure.com:443/'
STATION = 'bafc976f-1ccc-4a91-aaa6-c3eac2470d36'

token = os.environ['COSMOS_ACCESS_TOKEN']
expires = int(os.environ.get('COSMOS_ACCESS_TOKEN_EXPIRES', '0'))


class StaticToken:
    """Stand in for the credential the container cannot yet obtain."""

    def get_token(self, *scopes, **kwargs):
        """Return the token minted outside this process."""
        from azure.core.credentials import AccessToken

        return AccessToken(token, expires)


def static_credential():
    """Resolve to the injected token, in place of DefaultAzureCredential."""
    return StaticToken()


# Build the source in memory. Not saved: repointing the stored source at the
# live account would leave the dashboard pointed somewhere it cannot
# authenticate the moment this script exits.
source = HealthSource.objects.get(pk=1)
source.secret_ref = ''
source.config = dict(source.config, endpoint=LIVE_ENDPOINT)

connector = CosmosPumphouseConnector(source, station_uuid=STATION)
connector._credential = static_credential

print('endpoint:', LIVE_ENDPOINT)

ok, detail = connector.check()
print(f'check():  ok={ok} detail={detail!r}')

readings = connector.read_latest()
print(f'read_latest(): {len(readings)} readings')
for reading in readings[:5]:
    print(f'    {reading.external_key:<42} = {reading.value!r}')

end = timezone.now()
start = end - timedelta(hours=6)
samples = connector.read_window(
    '/dex/COMMAN_FORBAY_LEVEL', start, end, max_samples=4320
)
print(f'read_window(6h): {len(samples)} samples')
for sample in samples[:3]:
    print(f'    {sample.observed_at} = {sample.value!r}')
if samples:
    print(f'    ... last: {samples[-1].observed_at} = {samples[-1].value!r}')
