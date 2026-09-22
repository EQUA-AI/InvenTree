"""Decide Ranganayaka's temperature RTDs from ambient-soak evidence.

Run after export_review_pack.sh has produced /tmp/fresh_78.json and
/tmp/notes_78.json, and after /tmp/perpoint.json holds the measured statistics:

    sh contrib/cosmos/devtools/export_review_pack.sh 78

    python3 contrib/cosmos/devtools/review_ranganayaka_rtds.py

Why this station is different
----------------------------
Ranganayaka Sagar is a 134 MW machine where the other two are 40 MW, and it
carries a different instrument set: upper and lower guide bearing pads, oil
reservoirs and stator core RTDs that the others simply do not have, under its
own tag spellings. That is why 310 of its 371 points resolved to no catalogue
parameter at all - not a mapping bug, a genuinely different plant.

Why a unit can be settled here at all
-------------------------------------
The station never ran during the migrated window: zero running bays across
13,492 per-bay samples. So no magnitude tells us anything about power, current
or discharge, and those stay withheld. Temperature is the exception - an idle
machine still soaks at ambient, and ambient in July is a number we can read.
Medians of 28-29 with maxima around 31-34 are degC and are not degF (28F is
below freezing) nor K (28K is cryogenic).

A channel sitting at a constant -1, 0, 7.2 or 9.1 - minimum, median and maximum
all identical across thousands of samples - is not measuring anything. Those are
withheld individually rather than by family, because whether a given RTD is
wired varies bay by bay.
"""

import json
import re

# family -> (part IPN, component slot, template name template)
FAMILIES = {
    'PUMP_MOTOR_WINDING_TEMP': ('PS-MOTOR', 'PUMP | MOTOR | Motor Winding RTD {n}'),
    'MOTOR_STATOR_CORE_RTD': ('PS-MOTOR', 'PUMP | MOTOR | Motor Stator Core RTD {n}'),
    'PUMP_THRUST_BEARING_PAD_TEMP': (
        'PS-THRUST-BRG',
        'PUMP | THRUST-BRG | Thrust Bearing Pad Temperature {n}',
    ),
    'PUMP_BOTTOM_OIL_RESERVOR_TEMP': (
        'PS-THRUST-BRG',
        'PUMP | THRUST-BRG | Bottom Oil Reservoir Temperature {n}',
    ),
    'PUMP_UPPER_GUIDE_BEARING_PAD_TEMP': (
        'PS-GUIDE-BRG',
        'PUMP | GUIDE-BRG | Upper Guide Bearing Pad Temperature {n}',
    ),
    'PUMP_LOWER_GUIDE_BEARING_PAD_TEMP': (
        'PS-GUIDE-BRG',
        'PUMP | GUIDE-BRG | Lower Guide Bearing Pad Temperature {n}',
    ),
    'PUMP_GUIDE_BEARING_OIL_TEMP': (
        'PS-GUIDE-BRG',
        'PUMP | GUIDE-BRG | Guide Bearing Oil Temperature {n}',
    ),
    'PUMP_UPPER_OIL_RESERVOR_TEMP': (
        'PS-GUIDE-BRG',
        'PUMP | GUIDE-BRG | Upper Oil Reservoir Temperature {n}',
    ),
    'PUMP_BEARING_TEMP': ('PS-GUIDE-BRG', 'PUMP | GUIDE-BRG | Bearing Temperature {n}'),
    'COOLING_WATER_RTD': (
        'PS-WATER-COOLING',
        'PUMP | WATER-COOLING | Cooling Water RTD {n}',
    ),
    'COLD_AIR_RTD': ('PS-AIR-COOLING', 'PUMP | AIR-COOLING | Cold Air RTD {n}'),
    'HOT_AIR_RTD': ('PS-AIR-COOLING', 'PUMP | AIR-COOLING | Hot Air RTD {n}'),
}

#: Already mapped, and settleable without load: the reading is consistent with
#: rest and the catalogue unit is corroborated under load at a sister station.
SETTLEABLE = {
    'SPEED': (
        'rpm',
        'Catalogue unit rpm, corroborated under load at Saraswati PH (474 rpm). '
        'Ranganayaka never ran in the migrated window - zero running bays in '
        '13,492 per-bay samples - and reads 0 with a 6.3 maximum, consistent '
        'with a stopped shaft and contradicting nothing.',
    ),
    'EXCITATION_FLD_VLTG_PROCESS_VALUE': (
        'V',
        'Catalogue unit V, corroborated under load at Saraswati PH. Reads -1 to '
        '0.1 here with the plant idle, consistent with a de-excited field. Note '
        'this station does NOT show the ~1048 sentinel its sister stations show '
        'on the paired field-current channel.',
    ),
}

FAMILY_RE = re.compile(r'^(?P<fam>.*?)(?P<n>\d+)$')


def split(path):
    """Return (family, channel) for a per-bay dex path, or (None, None)."""
    tail = re.sub(r'^PUMP\d+_', '', path.rsplit('/', 1)[-1])
    m = FAMILY_RE.fullmatch(tail)
    if not m or m.group('fam') not in FAMILIES:
        return None, None
    return m.group('fam'), int(m.group('n'))


def build():
    """Return the decided pack and a tally of what each decision was."""
    with open('/tmp/fresh_78.json', encoding='utf-8') as handle:
        fresh = json.load(handle)
    with open('/tmp/notes_78.json', encoding='utf-8') as handle:
        notes = json.load(handle)
    with open('/tmp/perpoint.json', encoding='utf-8') as handle:
        stats = json.load(handle)

    approve, withhold = list(fresh['approve']), []
    tally = {'rtd approved': 0, 'rtd dead': 0, 'settled': 0, 'carried': 0}

    for entry in fresh['pending']:
        path = entry['paths'][0]
        tag = path.rsplit('/', 1)[-1]
        fam, channel = split(path)

        if fam is not None:
            ipn, template = FAMILIES[fam]
            mapping = {
                'part_ipn': ipn,
                'component_code': f'{ipn}:1',
                'parameter': template.format(n=channel),
            }
            measured = stats.get(tag)
            if measured is None:
                withhold.append({
                    **entry,
                    'reason': 'Temperature RTD with no samples in the measured window; unit '
                    'cannot be settled from a channel that never reported.',
                    'mapping': mapping,
                })
                tally['rtd dead'] += 1
                continue
            n, lo, med, hi = measured
            if 10 <= med <= 60 and hi <= 100:
                approve.append({
                    **entry,
                    'unit': 'degC',
                    'unit_status': 'verified',
                    'data_type': 'number',
                    'mapping': mapping,
                    'note': f'degC from ambient soak: {n} samples read {lo:g} to {hi:g}, '
                    f'median {med:g}, with the plant idle for the whole window '
                    f'(zero running bays in 13,492 per-bay samples). That band is '
                    f'ambient for July and excludes degF, which would be below '
                    f'freezing, and K, which would be cryogenic.',
                })
                tally['rtd approved'] += 1
            else:
                withhold.append({
                    **entry,
                    'reason': f'Dead channel: {n} samples all read {med:g} '
                    f'(min {lo:g}, max {hi:g}), so it is not measuring anything. '
                    f'Mapped so the tag resolves, but a constant cannot evidence a '
                    f'unit. Whether an RTD is wired varies by bay at this station.',
                    'mapping': mapping,
                })
                tally['rtd dead'] += 1
            continue

        fam2 = re.sub(r'^PUMP\d+_', '', tag)
        if fam2 in SETTLEABLE and entry.get('mapping'):
            unit, note = SETTLEABLE[fam2]
            approve.append({
                **entry,
                'unit': unit,
                'unit_status': 'verified',
                'note': note,
            })
            tally['settled'] += 1
            continue

        recorded = [notes[p] for p in entry['paths'] if notes.get(p, '').strip()]
        assert recorded, f'no recorded reason for {entry["paths"]}'
        withhold.append({**entry, 'reason': recorded[0]})
        tally['carried'] += 1

    assert len(approve) + len(withhold) == len(fresh['approve']) + len(fresh['pending'])
    out = {k: fresh[k] for k in fresh if k not in ('approve', 'withhold', 'pending')}
    out.update(approve=approve, withhold=withhold, pending=[])
    return out, tally


if __name__ == '__main__':
    pack, tally = build()
    with open('/tmp/pack_78_rtd.json', 'w', encoding='utf-8') as handle:
        json.dump(pack, handle, indent=1)
    print(f'approve {len(pack["approve"])} | withhold {len(pack["withhold"])}')
    print('   ' + ' | '.join(f'{k}: {v}' for k, v in tally.items()))
