"""Report how much of the binding set the emulator actually populates.

The question this answers is not "is the connector up" but "does every binding
the UI offers have anything behind it". A trend picker built over bindings that
never receive a sample lists parameters that cannot plot, which reads to a user
as a broken chart rather than an unpopulated fixture.

Local development diagnostic. Read-only.
"""

from collections import Counter
from datetime import timedelta

from django.utils import timezone

from assets.health_models import MachineSignalBinding, MachineSignalState
from assets.models import AssetMachine

FRESH_SECONDS = 300

station = AssetMachine.objects.get(pk=17)
family = [station, *AssetMachine.objects.filter(parent=station)]

bindings = MachineSignalBinding.objects.filter(machine__in=family)
states = MachineSignalState.objects.filter(binding__in=bindings)

total_bindings = bindings.count()
with_state = states.values('binding_id').distinct().count()

print(f'station         : {station.pk} {station.name}')
print(f'bindings        : {total_bindings}')
print(f'bindings w/state: {with_state}')
print(f'bindings empty  : {total_bindings - with_state}')

now = timezone.now()
fresh_cutoff = now - timedelta(seconds=FRESH_SECONDS)
fresh = states.filter(observed_at__gte=fresh_cutoff).count()
print(f'states fresh    : {fresh} (observed within {FRESH_SECONDS}s)')

newest = states.order_by('-observed_at').first()
oldest = states.order_by('observed_at').first()
print(f'newest observed : {newest.observed_at if newest else None}')
print(f'oldest observed : {oldest.observed_at if oldest else None}')
print(f'now             : {now}')

print('quality         :', dict(Counter(states.values_list('quality', flat=True))))

# Which bindings are still empty, grouped by their tag prefix, so a gap shows up
# as a family rather than 500 individual lines.
empty = bindings.exclude(id__in=states.values('binding_id'))
prefixes = Counter()
for key in empty.values_list('external_key', flat=True):
    parts = key.strip('/').split('/')
    prefixes['/'.join(parts[:2]) if len(parts) > 1 else parts[0]] += 1
if prefixes:
    print('empty by prefix :')
    for prefix, count in prefixes.most_common(10):
        print(f'   {prefix:<40} {count}')
else:
    print('empty by prefix : none - every binding has a state')
