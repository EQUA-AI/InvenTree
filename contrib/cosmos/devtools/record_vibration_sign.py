"""Replace the vibration unit question with what the data actually shows.

Run per station, after `sh contrib/cosmos/devtools/export_review_pack.sh <pk>`:

    python3 contrib/cosmos/devtools/record_vibration_sign.py <station_pk>

The 158 vibration points were withheld as a units question: "the catalogue
defines no unit and ISO 20816 admits both um and mm/s". That framing invites
someone to end it by choosing - pick mm/s, record why, approve. Measuring first
shows why that would be wrong.

Both candidates are *magnitudes*. Neither displacement amplitude in um nor
velocity RMS in mm/s can be negative. Between 10% and 34% of these readings are
negative, verbatim in the payload at full float precision - not a sentinel, not
a parse artefact:

    PUMP12_MTR_NDE_BRG_VBRTN1_PROCESS_VALUE = '-0.12116609513759613'
    PUMP14_MOTOR_DE_VIBRATION1              = '-0.1519097238779068'

So the channel is not emitting a vibration magnitude in either unit, and the
unit is not what blocks it. That makes this an instrumentation question -
Ask 1's kind - rather than a standards decision anyone here can take. Labelling
it mm/s would put a velocity on an operator's screen that goes negative.

What the numbers do say, kept because it is the half that is real: at 474 rpm
the conversion factor is 2*pi*f = 49.6, so the same motion reads about 20x
larger in um than in mm/s. Medians of 0.035-0.21 on stopped machines sit at a
velocity transducer's noise floor and an order below a proximity probe's
resolution, so if one had to be chosen, mm/s fits the small values better. It
cannot be chosen on that, because the reading that would settle it - one loaded
machine - does not exist in usable form anywhere in the estate: the only running
bay holds its whole block frozen at 59.257, itself a sentinel.
"""

import json
import re
import sys

#: The four vibration families. Deliberately not the thrust-pad RTDs, which
#: share the ISO wording in places but carry their own "fails under load" reason.
FAMILIES = re.compile(
    r'_(PMP_THRST_BRG_VBRTN\d+_PROCESS_VALUE'
    r'|MTR_NDE_BRG_VBRTN\d+_PROCESS_VALUE'
    r'|MOTOR_DE_VIBRATION\d+'
    r'|PUMP_MOTOR_DE_VIBRATION\d+)$'
)

REASON = (
    'The channel does not emit a vibration magnitude, so its unit is not what '
    'blocks it. Both candidates are magnitudes - displacement amplitude in um and '
    'velocity RMS in mm/s - and neither can be negative, yet 10-34% of these '
    'readings are, verbatim in the payload at full precision (e.g. '
    "'-0.12116609513759613'), which is neither a sentinel nor a parse artefact. "
    'This supersedes the earlier "ISO 20816 admits both um and mm/s" reason: that '
    'framing invited someone to settle it by choosing mm/s, which would put a '
    'velocity on screen that goes negative. It is an instrumentation question '
    '(BLOCKERS.md Ask 1) - is the signal rectified or RMS-converted at all, or is '
    'this a raw signed AI word? Measured 2026-09-26 across both stations that '
    'carry these tags. For the record: at 474 rpm the conversion factor is '
    '2*pi*f = 49.6, so the same motion reads about 20x larger in um than in mm/s, '
    'and medians of 0.035-0.21 on stopped machines fit a velocity transducer noise '
    'floor better than a proximity probe resolution - but nothing can be settled on '
    "that, because no usable loaded reading exists: the estate's only running bay "
    'holds its entire block frozen at the 59.257 sentinel.'
)


def build(pk):
    """Return the decided pack for one station, plus how many it restated."""
    with open(f'/tmp/fresh_{pk}.json', encoding='utf-8') as handle:
        fresh = json.load(handle)
    with open(f'/tmp/notes_{pk}.json', encoding='utf-8') as handle:
        notes = json.load(handle)
    assert not fresh['withhold'], 'a fresh export should list nothing as withheld'

    withhold, restated = [], 0
    for entry in fresh['pending']:
        path = entry['paths'][0]
        if FAMILIES.search(path):
            withhold.append({**entry, 'reason': REASON})
            restated += 1
            continue
        recorded = [notes[p] for p in entry['paths'] if notes.get(p, '').strip()]
        assert recorded, (
            f'no recorded reason for {entry["paths"]}; refusing to blank it'
        )
        withhold.append({**entry, 'reason': recorded[0]})

    out = {k: fresh[k] for k in fresh if k not in ('approve', 'withhold', 'pending')}
    out.update(approve=fresh['approve'], withhold=withhold, pending=[])
    return out, restated


if __name__ == '__main__':
    station = sys.argv[1]
    pack, restated = build(station)
    with open(f'/tmp/pack_vib_{station}.json', 'w', encoding='utf-8') as handle:
        json.dump(pack, handle, indent=1)
    print(
        f'station {station}: withhold {len(pack["withhold"])} '
        f'({restated} vibration points restated)'
    )
