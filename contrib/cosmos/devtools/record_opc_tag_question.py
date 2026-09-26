"""State precisely what is unknown about the OPC-UA node-id tags.

Run per station, after `sh contrib/cosmos/devtools/export_review_pack.sh <pk>`:

    python3 contrib/cosmos/devtools/record_opc_tag_question.py <station_pk>

Why these are not simply mapped
-------------------------------
The source spells bay-scoped measurements two ways: `PUMP5_...`, which the
catalogue keys on, and the raw node id of an OPC-UA subscription,
`...OS(2)::P5_...`, which it does not. All 120 of the latter carried one generic
reason - "No catalogue target: the tag resolves to no component or parameter" -
which is true and useless, because it does not say what would fix it.

They are not one problem. Three of every bay's ten are a second spelling of a
tag the catalogue already handles, and mapping those would give one catalogue
parameter two dictionary points, which makes approval ambiguous by construction;
of the two paths, the node id is also the one that stops responding. The other
seven carry measurements nothing else provides, and the obstacle there is not
the unit but the *meaning*: the abbreviations are the vendor's, absent from
published naming conventions, and the values do not identify a quantity. A
catalogue entry has to name a component and a physical kind, which is a stronger
claim than a unit and would rest on nothing.

So this records which of the two each point is, and for the second kind states
the question that answers it. Ownership is already settled - `registry.OPC_PUMP`
reads the bay out of the node id - so only the meaning is open.
"""

import json
import re
import sys

OPC = re.compile(r'^/dex/NS=.*::P([1-9][0-9]{0,3})_(.+)$')

DUPLICATE = (
    'Second spelling of a tag the catalogue already handles. This OPC-UA node id '
    'names the same measurement as /dex/PUMP{bay}_{local}_PROCESS_VALUE, and the two '
    'disagree - on a loaded bay this path reads 0 where the catalogued one reads '
    '59.257. Left unmapped deliberately: one catalogue parameter with two dictionary '
    'points makes approval ambiguous, and of the two paths this is the one that stops '
    'responding. Nothing to decide unless the plant says the node id is the '
    'trustworthy path, in which case the catalogued tag is what should be withheld.'
)

UNIQUE = (
    'Carries a measurement no other tag provides, but nothing states what it '
    'measures. "{local}" is a vendor abbreviation: it does not appear in published '
    'tag-naming conventions, and the observed values do not identify the quantity - a '
    'median around 35 fits degC, percent and metres equally. A catalogue entry must '
    'name a component and a physical kind, which is a stronger claim than a unit and '
    'would rest on nothing but the abbreviation. ASK THE PLANT: the OPC-UA tag list '
    'for PS1_PROG_19_4_2019, or simply what {local} measures on bay P{bay}. Ownership '
    'is settled - the node id names the bay - so the unit review can proceed the '
    'moment the meaning is known.'
)


def classify(paths):
    """Split the node-id paths into those that duplicate a catalogued tag, and the rest."""
    short = set(paths)
    duplicate, unique = {}, {}
    for path in paths:
        match = OPC.match(path)
        if match is None:
            continue
        bay, local = match[1], match[2]
        twin = f'/dex/PUMP{bay}_{local.replace(".", "_")}'
        (duplicate if twin in short else unique)[path] = (bay, local)
    return duplicate, unique


def build(pk):
    """Return the decided pack for one station, plus what it restated."""
    with open(f'/tmp/fresh_{pk}.json', encoding='utf-8') as handle:
        fresh = json.load(handle)
    with open(f'/tmp/notes_{pk}.json', encoding='utf-8') as handle:
        notes = json.load(handle)
    assert not fresh['withhold'], 'a fresh export should list nothing as withheld'

    every = [path for entry in fresh['pending'] for path in entry['paths']]
    every += [path for entry in fresh['approve'] for path in entry['paths']]
    duplicate, unique = classify(every)

    withhold, restated = [], {'duplicate': 0, 'unique': 0}
    for entry in fresh['pending']:
        path = entry['paths'][0]
        if path in duplicate:
            bay, local = duplicate[path]
            local = local.replace('.PROCESS_VALUE', '')
            withhold.append({**entry, 'reason': DUPLICATE.format(bay=bay, local=local)})
            restated['duplicate'] += 1
            continue
        if path in unique:
            bay, local = unique[path]
            local = local.replace('.PROCESS_VALUE', '')
            withhold.append({**entry, 'reason': UNIQUE.format(bay=bay, local=local)})
            restated['unique'] += 1
            continue
        recorded = [notes[p] for p in entry['paths'] if notes.get(p, '').strip()]
        assert recorded, (
            f'no recorded reason for {entry["paths"]}; refusing to blank it'
        )
        withhold.append({**entry, 'reason': recorded[0]})

    assert len(fresh['approve']) + len(withhold) == len(fresh['approve']) + len(
        fresh['pending']
    )
    out = {k: fresh[k] for k in fresh if k not in ('approve', 'withhold', 'pending')}
    out.update(approve=fresh['approve'], withhold=withhold, pending=[])
    return out, restated


if __name__ == '__main__':
    station = sys.argv[1]
    pack, restated = build(station)
    with open(f'/tmp/pack_opc_{station}.json', 'w', encoding='utf-8') as handle:
        json.dump(pack, handle, indent=1)
    print(
        f'station {station}: withhold {len(pack["withhold"])} '
        f'({restated["duplicate"]} duplicate spellings, '
        f'{restated["unique"]} awaiting a meaning, '
        f'{len(pack["withhold"]) - sum(restated.values())} carried forward)'
    )
