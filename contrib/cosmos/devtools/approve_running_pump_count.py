"""Map and approve `/pc`, the one station field the data identifies by itself.

Run per station, after `sh contrib/cosmos/devtools/export_review_pack.sh <pk>`:

    python3 contrib/cosmos/devtools/approve_running_pump_count.py <station_pk>

`/pc` carried "No catalogue target" because the importer never offered a
station-level field other than `st` to the matcher, and the catalogue defined
nothing for it. Both are now fixed - `registry.TOP_LEVEL_MAPPABLE_TAGS` and a
`Running Pump Count` parameter on the STATUS group - so this maps the three
existing points and approves them.

Why this one and not its neighbours
-----------------------------------
It is the only station field whose meaning the payload establishes without
reference to any name or document: `pc` equals the number of `pd` entries
reporting `st=R` in 72 of 72 sampled snapshots across all three stations,
including a 1-to-0 transition at Saraswati that rules out a constant. A count is
unitless, so there is no magnitude left to confirm - which is what blocks almost
everything else.

Its neighbours stay unmapped for reasons that have not changed. `pmw` and
`pmvar` restate the summed dex power tags and `dv` is the exact sum of the
per-bay discharge, so resolving any of them would give one catalogue parameter a
second dictionary point and make approval ambiguous. `sl` has no catalogue
parameter to resolve to.
"""

import json
import sys

MAPPING = {
    'part_ipn': 'PS-STATUS',
    'component_code': 'PS-STATUS:1',
    'parameter': 'PUMP | STATUS | Running Pump Count',
}

NOTE = (
    'Unitless count of bays reporting running. Identified from the payload alone, '
    'with no appeal to the tag name: /pc equals the number of pd entries with '
    'st=R in 72 of 72 sampled snapshots across all three stations, and Saraswati '
    'supplies the discriminating case - 1 while one bay runs, 0 while none do - so '
    'it tracks the count rather than sitting on a constant. A count has no unit to '
    'confirm, which is why this one could be settled while its neighbours could '
    'not. Approved 2026-09-26.'
)


def build(pk):
    """Return the decided pack for one station, and whether it found /pc."""
    with open(f'/tmp/fresh_{pk}.json', encoding='utf-8') as handle:
        fresh = json.load(handle)
    with open(f'/tmp/notes_{pk}.json', encoding='utf-8') as handle:
        notes = json.load(handle)
    assert not fresh['withhold'], 'a fresh export should list nothing as withheld'

    approve, withhold, promoted = list(fresh['approve']), [], 0
    for entry in fresh['pending']:
        if entry['paths'] == ['/pc']:
            approve.append({
                **entry,
                'data_type': 'number',
                'unit': '',
                'unit_status': 'unitless',
                'note': NOTE,
                'mapping': MAPPING,
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
    with open(f'/tmp/pack_pc_{station}.json', 'w', encoding='utf-8') as handle:
        json.dump(pack, handle, indent=1)
    print(
        f'station {station}: approve {len(pack["approve"])} (+{promoted} /pc) | '
        f'withhold {len(pack["withhold"])}'
    )
