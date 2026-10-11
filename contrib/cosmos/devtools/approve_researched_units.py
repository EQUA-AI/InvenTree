"""Approve the three tag families that published plant figures actually settle.

Run per station, after `sh contrib/cosmos/devtools/export_review_pack.sh <pk>`:

    python3 contrib/cosmos/devtools/approve_researched_units.py <station_pk>

What changed, and why it is not a guess
---------------------------------------
The estate is the Kaleshwaram scheme's barrage lifts. Two of its stations are
identified by their own forebay tag against published pond levels - Millbrook
reads 115.2 m below Annaram's 120.0 m, Cedar Creek 132.6 m at Sundilla's 130.0 m
- and by machine count (12 at Annaram, 14 at Sundilla, 4 at Ranganayaka Sagar).
That identification is what makes the published figures usable as evidence.

`dv` is cusecs. A published operating report for the Annaram pumphouse gives
four pumps yielding 11,724 cusecs, i.e. 2,931 each, and the tag reads exactly
2,931 while its bay runs. The earlier note excluded cusec because it derived
over 100% efficiency from a stated 34 m lift head; the published pond levels
show this lift is Annaram 120.0 m to Sundilla 130.0 m, so 34 m was never its
head. That was the contradiction BLOCKERS.md had already flagged.

`DISCHARGE_PRESSURE` is metres of water, not the proposed bar. At the observed
forebay level the static lift is 14.9 m, and the tag reads 18.96 - that lift
plus about 4 m of losses. Under bar it would be 193 m of head on a 15 m lift.

`ACTIVE_POWER` is MW, not the proposed kW - which its own catalogue parameter
already says, and which the 29 sibling points approved earlier already use.

What this deliberately does NOT do
----------------------------------
Nothing else is approved. 442 of the 950 pending points are constant across the
whole migrated window - dead channels, which no published figure can revive -
and the one bay that ever runs reports a frozen block whose current, frequency
and power factor are 0 *while running* and whose valve position is 118.5%. That
block cannot corroborate a magnitude for anything, so the families that depend
on it stay withheld with their recorded reasons carried forward unchanged.
"""

import json
import re
import sys

STATIONS = {
    '17': 'Parvathi (Sundilla)',
    '60': 'Saraswati (Annaram)',
    '78': 'Ranganayaka Sagar',
}

#: Only Saraswati ever ran in the migrated window, so only it carries the direct
#: magnitude evidence. The other two inherit the unit as the same field of the
#: same SCADA document schema - stated in the note rather than left implied.
DIRECT = '60'

EVIDENCE_DV = (
    "Cusecs (ft3/s), settled 2026-09-25 against the plant operator's own published "
    'operating figures. A published report for the Annaram pumphouse gives four pumps '
    'yielding 11,724 cusecs - 2,931 each - and this tag reads exactly 2931 while its '
    'bay runs, in 105 of 105 sampled snapshots, with one distinct value. The sibling '
    'figure for Medigadda (12,708 over six pumps = 2,118) does not match, so the '
    'match identifies the station as well as the unit. 2,931 ft3/s = 83.0 m3/s; '
    'against the 18.96 m discharge head and 24.5 MW input measured on the same bay '
    'that is 63% wire-to-water, and it is the only candidate that lands anywhere '
    'plausible - m3/s implies 22,000% efficiency, m3/min 37%, L/s 2%. This supersedes '
    'the earlier note excluding cusec: that derivation used a stated 34 m lift head, '
    'and the published pond levels (Annaram 120.0 m, Sundilla 130.0 m) show this lift '
    'is about 15 m, which is the contradiction BLOCKERS.md had already recorded '
    'against that figure. Caveat kept deliberately: the tag holds one value for a '
    'whole run, so it reports a steady design discharge rather than a live meter.'
)

EVIDENCE_DP = (
    'Metres of water column - NOT the proposed bar. Corrected 2026-09-25. The only '
    'running observation anywhere in the migrated estate is Saraswati bay P5 at '
    "18.962, constant across 105 of 105 sampled snapshots. That station's forebay "
    "reads 115.2 m and it lifts to Sundilla's published 130.0 m pond level, so its "
    'static lift at the observed levels is 14.9 m, and 18.96 m is that plus about 4 m '
    'of friction and velocity head - what a discharge transmitter should read. Under '
    'the proposed bar the same reading is 193 m of head on a 15 m lift, and under '
    'kg/cm2 186 m; both are impossible, so bar is excluded rather than merely '
    'unconfirmed. Converts to the catalogue unit at 1.86 bar. This also resolves the '
    'contradiction BLOCKERS.md recorded against a stated 34 m lift head - the '
    "published pond levels show 34 m is not this lift's head."
)

EVIDENCE_AP = (
    "MW, not the proposed kW. Corrected 2026-09-25. The catalogue's own Active Power "
    'parameter is MW and the 29 sibling points approved earlier on this estate are '
    "MW; these were the only points left proposing kW. At Saraswati the running bay's "
    'ACTIVE_POWER, its /pd/P5/pmw and the station /pmw all read 24.5 in the same '
    'snapshots with exactly one bay running, so the three are one quantity in one '
    'unit, and pmw is documented MW. The published rating for these machines is 40 MW '
    'each, against which 24.5 is 61% load; as kW it would be 0.06% of rating.'
)

INHERITED = (
    ' This station was idle for the whole migrated window, so the magnitude evidence '
    'above is from Saraswati; the unit is inherited as the same field of the same '
    'SCADA document schema across the estate, not re-measured here.'
)

FAMILIES = (
    (re.compile(r'^/pd/P\d+/dv$'), 'cusec', EVIDENCE_DV),
    (re.compile(r'^/dex/PUMP\d+_DISCHARGE_PRESSURE$'), 'mH2O', EVIDENCE_DP),
    (re.compile(r'^/dex/PUMP\d+_ACTIVE_POWER$'), 'MW', EVIDENCE_AP),
)


def decide(entry, pk):
    """Return the new unit and note for an entry, or None to leave it withheld."""
    path = entry['paths'][0]
    for pattern, unit, evidence in FAMILIES:
        if pattern.match(path):
            note = evidence if pk == DIRECT else evidence + INHERITED
            return unit, note
    return None


def build(pk):
    """Return the decided pack for one station, plus what it changed.

    Re-runnable: a family's decision is applied wherever the point appears, so a
    pack rebuilt after an earlier apply restates the same units rather than
    leaving whichever ones were written first.
    """
    with open(f'/tmp/fresh_{pk}.json', encoding='utf-8') as handle:
        fresh = json.load(handle)
    with open(f'/tmp/notes_{pk}.json', encoding='utf-8') as handle:
        notes = json.load(handle)
    assert not fresh['withhold'], 'a fresh export should list nothing as withheld'

    approve, withhold, promoted = [], [], 0

    for entry in fresh['approve']:
        decision = decide(entry, pk)
        if decision is None:
            approve.append(entry)
            continue
        unit, note = decision
        approve.append({**entry, 'unit': unit, 'unit_status': 'verified', 'note': note})

    for entry in fresh['pending']:
        decision = decide(entry, pk)
        if decision is not None and entry.get('mapping'):
            unit, note = decision
            approve.append({
                **entry,
                'unit': unit,
                'unit_status': 'verified',
                'note': note,
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
    with open(f'/tmp/pack_units_{station}.json', 'w', encoding='utf-8') as handle:
        json.dump(pack, handle, indent=1)
    print(
        f'station {station} ({STATIONS.get(station, "?")}): '
        f'approve {len(pack["approve"])} (+{promoted} newly settled) | '
        f'withhold {len(pack["withhold"])}'
    )
