"""Reshape the full PH_3 snapshot into the registry's accepted `rows` form.

`assets.registry.plan_dictionary` reads `{"rows": [...]}` where each row is a
Cassandra row: `entity_uuid`, the constant selectors, `time_period`,
`sub_time_period` and `data1`. The captured snapshot is an envelope
(`station_uuid` + `snapshots[]`) instead, so the keys have to be moved across.

This is a pure transform. Nothing is rounded, renamed, filled in or dropped, and
the `-242.1` and `3276.7` style readings are carried through untouched - clamping
them here would launder a data-quality question into a plausible-looking number.

The output hash is asserted because the import pins it: `import_dictionary`
refuses a file whose bytes differ from the one that was previewed. The trailing
newline is deliberate - `end-of-file-fixer` would otherwise add one on commit and
silently invalidate every stored hash.

Run from the repository root:

    python3 contrib/pump-cassandra/make_rows.py
"""

import collections
import hashlib
import json
import re

SOURCE = 'contrib/pump-cassandra/PH_3.full-snapshot.json'
TARGET = 'contrib/pump-cassandra/PH_3.full-snapshot.rows.json'
EXPECTED_SHA256 = '3861d5047510c02fc111782cff0c440b97e4379a71e22c4e63210f7aad52781a'

with open(SOURCE, encoding='utf-8') as fh:
    src = json.load(fh)
snap = src['snapshots'][0]
payload = snap['data1']
if isinstance(payload, str):
    payload = json.loads(payload)

row = {
    'entity_uuid': src['station_uuid'],
    'parent_entity_uuid': src['parent_entity_uuid'],
    # text in Cassandra, bigint for the sample; both are epoch milliseconds.
    'time_period': str(snap['time_period']),
    'sub_time_period': snap['sub_time_period'],
    **src['selectors'],
    'data1': payload,
}
raw = json.dumps({'rows': [row]}, separators=(',', ':')).encode('utf-8') + b'\n'
with open(TARGET, 'wb') as fh:
    fh.write(raw)

digest = hashlib.sha256(raw).hexdigest()
print('sha256:', digest, '(expected)' if digest == EXPECTED_SHA256 else '(CHANGED)')

# Coverage report. Ragged families are the cheap test of whether a transcribed
# payload is genuine plant data: pattern-completion fills every bay.
dex = payload['dex']
PUMP = re.compile(r'^PUMP([1-9][0-9]{0,3})_')
families = collections.defaultdict(set)
non_pump = []
for tag in dex:
    match = PUMP.match(tag)
    if match:
        families[tag[match.end() :]].add(int(match[1]))
    else:
        non_pump.append(tag)

bays = set(range(1, 15))
ragged = {f: sorted(bays - p) for f, p in families.items() if p != bays}
print('dex tags:', len(dex), '| families:', len(families))
print('non-pump keys:', non_pump)
print('pd keys:', sorted(payload['pd'], key=lambda k: int(k[1:])))
print('complete 1-14:', len(families) - len(ragged), '| ragged:', len(ragged))
for name, missing in sorted(ragged.items()):
    print(f'  {name}: missing {missing}')
