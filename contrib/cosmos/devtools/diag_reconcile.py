"""Reconcile the current-state cache against the historian for one signal.

The saturation sweep found none, yet the trend for PUMP5_..._TEMP5 returned the
pegged value at the newest timestamp. One of the two is lying about the plant and
it matters which: the table and the chart are supposed to agree.

Local development diagnostic. Read-only.
"""

from assets.health_models import MachineSignalBinding, MachineSignalState
from assets.models import AssetMachine
from machine_health.services.trends import read_trend

KEY = '/dex/PUMP5_PUMP_COOLING_WATER_INLET_TEMP5'

station = AssetMachine.objects.get(pk=17)
family = [station, *AssetMachine.objects.filter(parent=station)]

matches = MachineSignalBinding.objects.filter(machine__in=family, external_key=KEY)
print(f'bindings matching {KEY}: {matches.count()}')

for binding in matches:
    print(
        f'\nbinding pk={binding.pk} machine={binding.machine_id} '
        f'active={binding.active} unit={binding.unit!r} transform={binding.transform!r}'
    )

    state = MachineSignalState.objects.filter(binding=binding).first()
    if state is None:
        print('   state: NONE')
    else:
        print(
            f'   state.value   : {state.value!r}  (type {type(state.value).__name__})'
        )
        print(f'   state.quality : {state.quality}')
        print(f'   state.observed: {state.observed_at}')

    result = read_trend(binding.machine, binding_id=binding.pk)
    if result.get('available'):
        samples = result['samples']
        if samples:
            last = samples[-1]
            print(f'   trend last    : {last["value"]!r} at {last["observed_at"]}')
            print(f'   trend samples : {len(samples)}')
    else:
        print(f'   trend         : unavailable {result.get("reason")}')

# How many states hold a value that is not float-convertible - those would be
# skipped silently by the saturation sweep.
all_bindings = MachineSignalBinding.objects.filter(machine__in=family, active=True)
all_states = MachineSignalState.objects.filter(binding__in=all_bindings)
non_numeric = 0
numeric = 0
for state in all_states:
    try:
        float(state.value)
        numeric += 1
    except (TypeError, ValueError):
        non_numeric += 1
print(f'\nstates numeric: {numeric}  non-numeric/skipped: {non_numeric}')

# Largest current values, to see what the top of the range actually looks like.
values = []
for state in all_states.select_related('binding'):
    try:
        values.append((float(state.value), state.binding.external_key))
    except (TypeError, ValueError):
        continue
values.sort(reverse=True)
print('\nlargest current values:')
for value, key in values[:8]:
    print(f'   {value:>22} {key}')
