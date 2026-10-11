"""Exercise the real trend path for bindings the excerpt could never populate.

Counting rows in MachineSignalState is not enough: that table is a current-state
cache, while a trend is a federated read straight to the historian. A binding can
have a perfectly good current value and still plot nothing. This calls the same
service the API calls, so what it prints is what the chart will draw.

Local development diagnostic. Read-only.
"""

from assets.health_models import MachineSignalBinding
from assets.models import AssetMachine
from machine_health.services.trends import read_trend

# Keys chosen because they were previously empty under the trimmed excerpt, plus
# one that was already populated as a control.
PROBE_KEYS = [
    '/dex/PUMP4_PUMP_COOLING_WATER_INLET_TEMP1',
    '/dex/PUMP14_PUMP_COOLING_WATER_INLET_TEMP1',
    '/dex/PUMP7_PUMP_COOLING_WATER_INLET_TEMP1',
    '/dex/COMMAN_FORBAY_LEVEL',
    '/dex/PUMP5_PUMP_COOLING_WATER_INLET_TEMP5',
]

station = AssetMachine.objects.get(pk=17)
family = [station, *AssetMachine.objects.filter(parent=station)]

for key in PROBE_KEYS:
    binding = MachineSignalBinding.objects.filter(
        machine__in=family, external_key=key, active=True
    ).first()
    if binding is None:
        print(f'{key}\n    NO BINDING')
        continue

    result = read_trend(binding.machine, binding_id=binding.pk)
    if not result.get('available'):
        print(
            f'{key}\n    unavailable: {result.get("reason")} - {result.get("detail")}'
        )
        continue

    samples = result['samples']
    values = [s['value'] for s in samples if isinstance(s['value'], (int, float))]
    times = [s['observed_at'] for s in samples]
    ascending = times == sorted(times)

    print(f'{key}')
    print(
        f'    unit={result["unit"]!r} samples={len(samples)} '
        f'numeric={len(values)} truncated={result.get("truncated")}'
    )
    if times:
        print(f'    first={times[0]}  last={times[-1]}  ascending={ascending}')
    if values:
        distinct = len(set(values))
        print(f'    min={min(values)} max={max(values)} distinct={distinct}')
        if distinct == 1:
            print('    NOTE: every sample identical - will render as a flat line')
