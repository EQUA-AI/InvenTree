"""How many live signals are currently serving a saturated register value.

3276.699951171875 is 32767 x 0.1 in float32 - the signed 16-bit maximum. It is a
register that has pegged, not a reading. Before the trend chart existed this hid
in a table cell; now it draws as a clean, confident flat line labelled degC, which
is a more persuasive way to be wrong.

Note on MachineSignalState.value: it is a *dict* like
``{'unit': 'degC', 'value': 3276.7}``, not a scalar. An earlier version of this
script called float() on it directly, which raised for every row, skipped all 581
states and then printed "0 pegged" - a sweep that inspects nothing looks exactly
like a sweep that found nothing. Hence the coverage assertion below.

Local development diagnostic. Read-only.
"""

from collections import Counter

from assets.health_models import MachineSignalBinding, MachineSignalState
from assets.models import AssetMachine

SATURATED = 3276.699951171875
UNDER_RANGE = (-242.1, -118.5, -50.1, -47.9, -41.9)


def scalar(raw):
    """Unwrap the stored value envelope and return a float, or None."""
    if isinstance(raw, dict):
        raw = raw.get('value')
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


station = AssetMachine.objects.get(pk=17)
family = [station, *AssetMachine.objects.filter(parent=station)]

bindings = MachineSignalBinding.objects.filter(machine__in=family, active=True)
states = list(
    MachineSignalState.objects.filter(binding__in=bindings).select_related('binding')
)

readable = [(s, scalar(s.value)) for s in states]
numeric = [(s, v) for s, v in readable if v is not None]
skipped = len(readable) - len(numeric)

print(f'active bindings : {bindings.count()}')
print(f'states inspected: {len(numeric)}   unreadable: {skipped}')

if not numeric:
    raise SystemExit(
        'ABORT: no state value could be read as a number. Any "all clear" below '
        'would be an artefact of the sweep, not a fact about the plant.'
    )

pegged = [(s, v) for s, v in numeric if abs(v - SATURATED) < 1e-6]
print(f'pegged at 32767x0.1: {len(pegged)}')

if pegged:
    verdicts = Counter()
    for state, value in pegged:
        verdicts[str(state.binding.classify(value))] += 1
    print('\nwould the binding limits catch it?')
    for verdict, count in verdicts.most_common():
        print(f'   {verdict:<24} {count}')

    print('\npegged points:')
    for state, _ in pegged[:15]:
        binding = state.binding
        print(
            f'   {binding.external_key:<54} unit={binding.unit!r} '
            f'crit_max={binding.critical_max} warn_max={binding.warn_max}'
        )

print('\nunder-range suspects:')
for suspect in UNDER_RANGE:
    hits = [(s, v) for s, v in numeric if abs(v - suspect) < 0.05]
    if hits:
        print(f'   {suspect:>8} : {len(hits)} signal(s)')
        for state, _ in hits[:4]:
            print(f'              {state.binding.external_key}')

values = sorted(((v, s.binding.external_key) for s, v in numeric), reverse=True)
print('\nlargest current values:')
for value, key in values[:8]:
    print(f'   {value:>22} {key}')
print('\nsmallest current values:')
for value, key in values[-5:]:
    print(f'   {value:>22} {key}')
