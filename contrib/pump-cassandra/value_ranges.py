"""Evidence for unit review: what do the real values actually look like?

A proposed unit is a hypothesis about magnitude. `PUMP_LINE_TO_LINE_VOLTAGE` in
volts predicts ~11000; in kilovolts it predicts ~11. The snapshot can therefore
falsify a unit outright, which is worth more than a plausible-sounding label.

Reports per family across all bays: count, how many are null, and the observed
range. Nothing is clamped or excluded - the out-of-range readings are the point.
"""

import collections
import json
import re
import statistics

SOURCE = 'contrib/pump-cassandra/PH_3.full-snapshot.json'

with open(SOURCE, encoding='utf-8') as fh:
    src = json.load(fh)

payload = src['snapshots'][0]['data1']
if isinstance(payload, str):
    payload = json.loads(payload)

PUMP = re.compile(r'^PUMP([1-9][0-9]{0,3})_')
families = collections.defaultdict(list)

for tag, value in payload['dex'].items():
    if tag in {'ID', 'TIMESTAMP'}:
        continue
    match = PUMP.match(tag)
    families[tag[match.end() :] if match else tag].append(value)

# Station envelope and per-pump summary keys.
for key in ('sl', 'pc', 'dv', 'pmw', 'pmvar', 'st'):
    if key in payload:
        families['(envelope) ' + key].append(payload[key])
for summary in payload['pd'].values():
    for key, value in summary.items():
        families['(pd) ' + key].append(value)


def number(value):
    """Read a SCADA numeric that may arrive as a string. None if it is not one."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


print(f'{"family":<50} {"n":>3} {"null":>4} {"min":>12} {"median":>11} {"max":>12}')
print('-' * 98)
for name in sorted(families):
    values = families[name]
    numbers = [n for n in (number(v) for v in values) if n is not None]
    nulls = sum(1 for v in values if v is None)
    if not numbers:
        kinds = {type(v).__name__ for v in values}
        distinct = sorted({str(v) for v in values})[:4]
        print(
            f'{name[:50]:<50} {len(values):>3} {nulls:>4}   non-numeric {kinds} {distinct}'
        )
        continue
    print(
        f'{name[:50]:<50} {len(values):>3} {nulls:>4} '
        f'{min(numbers):>12.4f} {statistics.median(numbers):>11.4f} {max(numbers):>12.4f}'
    )
