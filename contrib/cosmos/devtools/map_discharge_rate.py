"""Give the `dv` discharge tags a catalogue home without asserting their unit.

Run per station, after `sh /tmp/exp.sh <pk>` has produced /tmp/fresh_<pk>.json
(the review pack) and /tmp/notes_<pk>.json (every reason currently recorded):

    python3 contrib/cosmos/devtools/map_discharge_rate.py <station_pk>

Why this exists
---------------
`dv` is the only measurement of pump discharge the source carries, and it is
identified beyond reasonable doubt: it reads the station's stated design
discharge while a bay runs and exactly zero while it does not, and the
station-level `/dv` is the exact sum of its bays in every sampled snapshot.

What nothing confirms is its *unit*. So the point is mapped and deliberately not
approved - the review machinery treats those as separate claims, and only
approved points are ever bound to the live dashboard. The flow total therefore
stays "incomplete" rather than displaying a number that could be wrong by a
factor of sixty.

The per-bay tags are mapped; the station-level `/dv` is not. The catalogue's
discharge parameter belongs to a *pump's discharge line*, and the station value
is a plant-wide sum rather than a line measurement, so mapping it there would
record something untrue about the plant.

Every other reason already recorded is carried forward from the database, never
from the export: a fresh export re-lists withheld points as `pending` with no
reason at all, and applying that would silently erase them.
"""

import json
import re
import sys

MAPPING = {
    'part_ipn': 'PS-DISCHARGE',
    'component_code': 'PS-DISCHARGE:1',
    'parameter': 'PUMP | DISCHARGE | Discharge Rate',
}

PER_BAY_REASON = (
    'Identified with certainty; unit unconfirmed. The tag reads the stated design '
    'discharge while its bay runs and exactly 0 while it does not, and the station '
    '/dv is the exact sum of its bays in 2,279 of 2,279 sampled snapshots. Mapped '
    'to the catalogue on 2026-09-22 so the tag resolves to a parameter, but '
    'deliberately NOT approved, because no source states the unit. Pump hydraulics '
    'exclude m3/s, m3/h and L/s outright, and exclude cusec at Parvathi and '
    'Saraswati (both would need over 100% efficiency); m3/min is the only survivor, '
    'but it implies 47-72% efficiency and that derivation rests on lift_head, which '
    'BLOCKERS.md records as contradicted by DISCHARGE_PRESSURE. The station flow '
    'total stays incomplete until the plant confirms the unit; once it does, this '
    'becomes an approval and nothing else.'
)

STATION_REASON = (
    'Station total discharge: the exact sum of the per-bay dv in 2,279 of 2,279 '
    'sampled snapshots. Left unmapped on purpose - the catalogue parameter for '
    'discharge rate belongs to a pump discharge line, and this is a plant-wide sum '
    'rather than a line measurement, so filing it there would record something '
    'untrue about the plant. It carries the same unresolved unit as the per-bay '
    'tags it sums.'
)


def build(pk):
    """Return the decided pack for one station, and a count of what changed."""
    with open(f'/tmp/fresh_{pk}.json', encoding='utf-8') as handle:
        fresh = json.load(handle)
    with open(f'/tmp/notes_{pk}.json', encoding='utf-8') as handle:
        notes = json.load(handle)
    assert not fresh['withhold'], 'a fresh export should list nothing as withheld'

    approve = list(fresh['approve'])
    withhold, mapped, station_level = [], 0, 0

    for entry in fresh['pending']:
        path = entry['paths'][0]
        if re.fullmatch(r'/pd/P[1-9][0-9]*/dv', path):
            withhold.append({**entry, 'reason': PER_BAY_REASON, 'mapping': MAPPING})
            mapped += 1
            continue
        if path == '/dv':
            withhold.append({**entry, 'reason': STATION_REASON})
            station_level += 1
            continue
        recorded = [notes[p] for p in entry['paths'] if notes.get(p, '').strip()]
        assert recorded, (
            f'no recorded reason for {entry["paths"]}; refusing to blank it'
        )
        withhold.append({**entry, 'reason': recorded[0]})

    assert len(approve) + len(withhold) == len(fresh['approve']) + len(fresh['pending'])
    out = {k: fresh[k] for k in fresh if k not in ('approve', 'withhold', 'pending')}
    out.update(approve=approve, withhold=withhold, pending=[])
    return out, mapped, station_level


if __name__ == '__main__':
    station = sys.argv[1]
    pack, mapped, station_level = build(station)
    with open(f'/tmp/pack_dv_{station}.json', 'w', encoding='utf-8') as handle:
        json.dump(pack, handle, indent=1)
    print(
        f'station {station}: approve {len(pack["approve"])} | '
        f'withhold {len(pack["withhold"])} '
        f'({mapped} per-bay dv mapped, {station_level} station-level dv left unmapped, '
        f'{len(pack["withhold"]) - mapped - station_level} reasons carried forward)'
    )
