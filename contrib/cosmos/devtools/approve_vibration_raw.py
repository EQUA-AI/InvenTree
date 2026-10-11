"""Approve the vibration channels as raw, unitless source values.

Run per station, after `sh contrib/cosmos/devtools/export_review_pack.sh <pk>`:

    python3 contrib/cosmos/devtools/approve_vibration_raw.py <station_pk>

then apply `/tmp/pack_vib_<pk>.json` with `apply_dictionary_review` and
re-activate the station so the new points are bound.

What this decides, and what it does not
---------------------------------------
The 158 vibration points were withheld because the source does not say what
they measure: both candidate units are magnitudes, and 10-34% of the readings
are negative, so neither um nor mm/s can honestly be written on them
(``record_vibration_sign.py`` has the measurement). That finding stands and is
kept on every point.

The operator has asked to see the channels anyway, so they are approved the
one way that asserts nothing false: as *unitless* raw source values. The
Health blade and the Performance tab draw them without a unit and say so in
words. No threshold is set on them - a limit without a unit is not a limit -
and the instrumentation question (BLOCKERS.md Ask 1) remains the thing that
would let a unit be written later. When it is answered, a re-review can set
``unit_status`` to ``verified`` with the unit; the bindings then refresh in
place.

Every other pending point is re-recorded as withheld with the reason already
on it, exactly as the earlier approvals did, so nothing is silently blanked.
"""

import json
import re
import sys

#: The four vibration families, as `record_vibration_sign.py` defines them. The
#: OPC-addressed copies at Millbrook end in `.PV` and are deliberately outside.
FAMILIES = re.compile(
    r'_(PMP_THRST_BRG_VBRTN\d+_PROCESS_VALUE'
    r'|MTR_NDE_BRG_VBRTN\d+_PROCESS_VALUE'
    r'|MOTOR_DE_VIBRATION\d+'
    r'|PUMP_MOTOR_DE_VIBRATION\d+)$'
)

DECISION = (
    " Approved 2026-09-26 as a raw, unitless source value at the operator's "
    'request, so the channel can be watched on the Health and Performance '
    'pages. This is not a unit decision: the value is shown without a unit, '
    'may be signed, and carries no threshold. Ask 1 stands; a confirmed unit '
    'can be written in a later review.'
)


def build(pk):
    """Return the decided pack for one station, and how many were promoted."""
    with open(f'/tmp/fresh_{pk}.json', encoding='utf-8') as handle:
        fresh = json.load(handle)
    with open(f'/tmp/notes_{pk}.json', encoding='utf-8') as handle:
        notes = json.load(handle)
    assert not fresh['withhold'], 'a fresh export should list nothing as withheld'

    approve, withhold, promoted = list(fresh['approve']), [], 0
    for entry in fresh['pending']:
        if all(FAMILIES.search(path) for path in entry['paths']):
            assert entry.get('mapping'), f'{entry["paths"]} has no catalogue mapping'
            assert entry['data_type'] == 'number', entry['paths']
            existing = (entry.get('note') or '').strip()
            approve.append({
                **entry,
                'unit': '',
                'unit_status': 'unitless',
                'note': (existing + DECISION).strip(),
            })
            promoted += 1
            continue
        recorded = [notes[p] for p in entry['paths'] if notes.get(p, '').strip()]
        assert recorded, (
            f'no recorded reason for {entry["paths"]}; refusing to blank it'
        )
        withhold.append({**entry, 'reason': recorded[0]})

    assert len(approve) + len(withhold) == len(fresh['approve']) + len(fresh['pending'])
    out = {k: fresh[k] for k in fresh if k not in ('approve', 'withhold', 'pending')}
    out.update(approve=approve, withhold=withhold, pending=[])
    return out, promoted


if __name__ == '__main__':
    station = sys.argv[1]
    pack, promoted = build(station)
    with open(f'/tmp/pack_vib_{station}.json', 'w', encoding='utf-8') as handle:
        json.dump(pack, handle, indent=1)
    print(
        f'station {station}: approve {len(pack["approve"])} (+{promoted} vibration) | '
        f'withhold {len(pack["withhold"])}'
    )
