"""Settle Parvathi's and Saraswati's temperature channels from measured bands.

    sh contrib/cosmos/devtools/export_review_pack.sh 60
    python3 contrib/cosmos/devtools/review_temperatures.py 60

Reads /tmp/fresh_<pk>.json, /tmp/notes_<pk>.json and /tmp/stats_<pk>.json, the
last being per-tag statistics split by whether that tag's own bay was running.

What the load column settles
----------------------------
Saraswati ran one bay throughout the migrated window, so its tags can be read
twice: at rest and under load. That splits the temperature channels into three
groups which look identical if you only ever see them stopped.

* Clean in both states - approved, with the unit confirmed under load.
* Pinned to 3277 whenever a bay runs, sane at rest - the 16-bit over-range
  sentinel. The unit is degC and the instrument is broken, which are different
  findings: these are mapped so the tag resolves, and withheld.
* Never resolving to a catalogue parameter at all, because the catalogue did
  not model that measurement until now.

Parvathi never ran, so only the at-rest band is available there. That is enough
for a unit - an idle machine soaks at ambient, and 28-29 is degC, not degF and
not K - but not enough to clear a channel that its sister station shows failing
under load. Where Saraswati proves a family pins, Parvathi's equivalent is
approved on the unit and carries the sister-station caveat in its note, because
Parvathi has never been observed running at all.
"""

import json
import re
import sys

SENTINEL = 3276.7  # 32767 / 10, the 16-bit over-range value

#: Families this pass decides, and the catalogue parameter each maps to.
#: A `None` mapping means the point is already mapped and only needs deciding.
FAMILIES = {
    # Saraswati, newly modelled and clean under load
    'PUMP_BRUSH_GEARD2': ('PS-MOTOR', 'PUMP | MOTOR | Brush Gear Temperature'),
    'PUMP_SHELL_RIGHTD1': ('PS-MOTOR', 'PUMP | MOTOR | Motor Shell Temperature Right'),
    'PUMP_COOLING_AIR_D_END_LEFT_COLDD15': (
        'PS-AIR-COOLING',
        'PUMP | AIR-COOLING | Cooling Air DE Left Cold',
    ),
    'PUMP_COOLING_AIR_D_END_RIGHT_COLDD16': (
        'PS-AIR-COOLING',
        'PUMP | AIR-COOLING | Cooling Air DE Right Cold',
    ),
    'PUMP_COOLING_AIR_ND_END_LEFT_COLDD13': (
        'PS-AIR-COOLING',
        'PUMP | AIR-COOLING | Cooling Air NDE Left Cold',
    ),
    'PUMP_COOLING_AIR_ND_END_RIGHT_COLDD14': (
        'PS-AIR-COOLING',
        'PUMP | AIR-COOLING | Cooling Air NDE Right Cold',
    ),
    'PUMP_COOLING_WATER_COOL_OUTLET_LEFTD17': (
        'PS-WATER-COOLING',
        'PUMP | WATER-COOLING | Cooler Water Outlet Left',
    ),
    'PUMP_COOLING_WATER_COOL_OUTLET_RIGHTD18': (
        'PS-WATER-COOLING',
        'PUMP | WATER-COOLING | Cooler Water Outlet Right',
    ),
    'PUMP_PUMP_OUTLET_COOLING_WATER_TEMP_1D4': (
        'PS-WATER-COOLING',
        'PUMP | WATER-COOLING | Outlet Cooling Water Temperature D4',
    ),
    # Saraswati, newly modelled and failing under load
    'PUMP_SHELL_LEFTD23': ('PS-MOTOR', 'PUMP | MOTOR | Motor Shell Temperature Left'),
    'PUMP_SHELL_SUMPD24': ('PS-MOTOR', 'PUMP | MOTOR | Motor Shell Temperature Sump'),
    'PUMP_MOTOR_WINDING_TEMPERATURED12': (
        'PS-MOTOR',
        'PUMP | MOTOR | Motor Winding Temperature 12',
    ),
    # Already mapped at both stations; only the decision was missing.
    'PUMP_GUIDED_RADIAL_PAD_1D6': None,
    'PUMP_GUIDED_RADIAL_PAD_2D6': None,
    'PUMP_THRUST_AXIAL_PAD_1D5': None,
    'PUMP_THRUST_AXIAL_PAD_2D5': None,
}

CHANNELLED = {
    'MT_STTR_WND_RTD': (
        'PS-MOTOR',
        'PUMP | MOTOR | Stator Winding RTD {n}',
        r'MT_STTR_WND_RTD(\d+)_PROCESS_VALUE',
    ),
    'COOLING_OIL_OUTLET_TEMP': (
        'PS-THRUST-BRG',
        'PUMP | THRUST-BRG | Cooling Oil Outlet Temperature {n}',
        r'COOLING_OIL_OUTLET_TEMP(\d+)',
    ),
}


def mapping_for(family):
    """Return the crosswalk for a family, or None if it is already mapped."""
    if family in FAMILIES:
        target = FAMILIES[family]
        if target is None:
            return None
        ipn, parameter = target
        return {'part_ipn': ipn, 'component_code': f'{ipn}:1', 'parameter': parameter}
    for _prefix, (ipn, template, pattern) in CHANNELLED.items():
        m = re.fullmatch(pattern, family)
        if m:
            return {
                'part_ipn': ipn,
                'component_code': f'{ipn}:1',
                'parameter': template.format(n=int(m.group(1))),
            }
    return None


def handled(family):
    """Whether this pass has an opinion about the family."""
    return family in FAMILIES or any(
        re.fullmatch(p, family) for _, (_, _, p) in CHANNELLED.items()
    )


def decide(pk):
    """Return the decided pack and a tally of the decisions taken."""
    with open(f'/tmp/fresh_{pk}.json', encoding='utf-8') as handle:
        fresh = json.load(handle)
    with open(f'/tmp/notes_{pk}.json', encoding='utf-8') as handle:
        notes = json.load(handle)
    with open(f'/tmp/stats_{pk}.json', encoding='utf-8') as handle:
        stats = json.load(handle)

    # Only one bay ran at Saraswati, so most tags have no load samples of their
    # own. The sentinel is a property of the channel type, not of the bay that
    # happened to be running, so the verdict is reached per family and applied
    # to every bay: approving the other eleven would publish channels that pin
    # the moment those bays start.
    pinned = set()
    for tag, measured in stats.items():
        load = measured.get('run')
        if load and abs(load[2] - SENTINEL) < 1:
            pinned.add(re.sub(r'^PUMP\d+_', '', tag))

    approve, withhold = list(fresh['approve']), []
    tally = {'approved': 0, 'pins under load': 0, 'no band': 0, 'carried': 0}

    for entry in fresh['pending']:
        tag = entry['paths'][0].rsplit('/', 1)[-1]
        family = re.sub(r'^PUMP\d+_', '', tag)
        measured = stats.get(tag) or {}
        rest, load = measured.get('stop'), measured.get('run')

        if not handled(family) or not rest:
            recorded = [notes[p] for p in entry['paths'] if notes.get(p, '').strip()]
            assert recorded, f'no recorded reason for {entry["paths"]}'
            withhold.append({**entry, 'reason': recorded[0]})
            tally['carried'] += 1
            continue

        crosswalk = mapping_for(family)
        extra = {'mapping': crosswalk} if crosswalk else {}
        n, lo, med, hi = rest

        if not 10 <= med <= 70:
            withhold.append({
                **entry,
                **extra,
                'reason': f'No usable band at rest: {n} samples read {lo:g} to {hi:g}, median '
                f'{med:g}, which is not a temperature. The unit cannot be settled '
                f'from it.',
            })
            tally['no band'] += 1
            continue

        if family in pinned:
            seen = 'this bay' if load else 'the one bay that ran'
            withhold.append({
                **entry,
                **extra,
                'reason': f'Unit is degC, instrument is not. At rest it reads {lo:g} to {hi:g} '
                f'(median {med:g}), which is ambient and excludes degF and K. But on '
                f'{seen} this channel reads the 3277 over-range sentinel on every '
                f'sample taken while the bay was running, so it fails exactly when '
                f'the machine is working. Only one bay ran during the window, and '
                f'the sentinel is a property of the channel rather than of that bay, '
                f'so the finding is applied to all of them. Mapped so the tag '
                f'resolves; not approved, because approving it would publish a fault '
                f'code under load.',
            })
            tally['pins under load'] += 1
            continue

        if load:
            note = (
                f'degC confirmed in both states: {load[0]} samples with the bay '
                f'running read a median of {load[2]:g}, and {n} at rest read '
                f'{lo:g} to {hi:g} (median {med:g}). Both bands are ambient-to-'
                f'working temperature and exclude degF and K.'
            )
        else:
            note = (
                f'degC from ambient soak: {n} samples read {lo:g} to {hi:g}, '
                f'median {med:g}, with this station idle for the whole window. '
                f'That band excludes degF, which would be near freezing, and K, '
                f'which would be cryogenic. No bay here has ever been observed '
                f'running, so the channel is unproven under load - at Saraswati '
                f'some channels of this kind pin to a 3277 sentinel when loaded.'
            )
        approve.append({
            **entry,
            **extra,
            'unit': 'degC',
            'unit_status': 'verified',
            'data_type': 'number',
            'note': note,
        })
        tally['approved'] += 1

    assert len(approve) + len(withhold) == len(fresh['approve']) + len(fresh['pending'])
    out = {k: fresh[k] for k in fresh if k not in ('approve', 'withhold', 'pending')}
    out.update(approve=approve, withhold=withhold, pending=[])
    return out, tally


if __name__ == '__main__':
    station = sys.argv[1]
    pack, tally = decide(station)
    with open(f'/tmp/pack_temp_{station}.json', 'w', encoding='utf-8') as handle:
        json.dump(pack, handle, indent=1)
    print(
        f'station {station}: approve {len(pack["approve"])} | '
        f'withhold {len(pack["withhold"])}'
    )
    print('   ' + ' | '.join(f'{k}: {v}' for k, v in tally.items()))
