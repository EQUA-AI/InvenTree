"""Point the dashboard's Cosmos source at the live account, or back again.

Reversible on purpose. Switching endpoints is the kind of change that is easy to
make and then hard to remember, and a source left pointing at an account it
cannot reach reports as a dead connector rather than a misconfiguration.

    python manage.py shell < .../repoint_source.py      # shows current state
    REPOINT=live  ... # switch to Azure
    REPOINT=emulator ... # switch back

Against the live account ``secret_ref`` must be empty: the connector refuses a
key credential outside the emulator, so a stale ref raises CosmosConfigError
rather than silently falling back to Entra ID.
"""

import json
import os

from assets.health_models import HealthSource

LIVE_ENDPOINT = 'https://epconchatcosmos9d6b.documents.azure.com:443/'
EMULATOR_ENDPOINT = 'http://cosmos-emulator:8081'
EMULATOR_SECRET_REF = 'COSMOS_EMULATOR_KEY'

source = HealthSource.objects.get(pk=1)
mode = os.environ.get('REPOINT', '').strip().lower()

print('current:')
print('   name:       ', source.name)
print('   endpoint:   ', source.config.get('endpoint'))
print('   secret_ref: ', repr(source.secret_ref))
print('   database:   ', source.config.get('database'))
print('   container:  ', source.config.get('readings_container'))

if not mode:
    print('\nno REPOINT set; nothing changed.')
elif mode == 'live':
    source.config = dict(source.config, endpoint=LIVE_ENDPOINT)
    source.secret_ref = ''
    source.name = 'Cosmos (Azure live)'
    source.save()
    print('\nrepointed to LIVE. secret_ref cleared so Entra ID is used.')
elif mode == 'emulator':
    source.config = dict(source.config, endpoint=EMULATOR_ENDPOINT)
    source.secret_ref = EMULATOR_SECRET_REF
    source.name = 'Cosmos emulator (local dev)'
    source.save()
    print('\nrepointed to EMULATOR.')
else:
    raise SystemExit(f'unknown REPOINT={mode!r}; use "live" or "emulator"')

source.refresh_from_db()
print(
    'now:',
    source.name,
    '|',
    source.config.get('endpoint'),
    '| secret_ref=',
    repr(source.secret_ref),
)
print('full config:', json.dumps(source.config, indent=2, default=str))
