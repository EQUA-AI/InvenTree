# Dictionary review packs

The reviewed dictionary for the three pumphouse stations, exported so it can be
replayed onto another deployment instead of copying database rows.

None of this is telemetry. The readings live in the Cosmos container; what these
files carry is the layer of meaning around them - which source tag is which
catalogue parameter, what unit it is in, what it is called on screen, and why
each decision was made. That layer exists only in a database until it is
exported, and a fresh deployment has none of it.

## Why not a database dump

A dump carries primary keys, and a deployment that already holds other assets
will collide on them. These packs resolve identity the way the registry does -
`source_entity_uuid` for the station, `source_key` for the bay, part IPN and
component code for the catalogue - so they apply to a database that already has
its own machines, parts and users without touching any of them.

## Bootstrapping a deployment

One command, run inside the deployed container:

    python manage.py load_pump_catalogue
    python manage.py discover_data_range --source <pk> --from 2025-07-01 --to 2025-07-13
    python manage.py onboard_pumphouse_estate contrib/cosmos/review/estate.manifest.json --source <pk> --dry-run
    python manage.py onboard_pumphouse_estate contrib/cosmos/review/estate.manifest.json --source <pk> --activate

`discover_data_range` must come before `--activate`. Activation reads the
recorded window to decide where each ingestion cursor starts, and a cursor only
moves forward: activate first and it is placed at the wall clock, which for a
recorded window is past every document the source will return, and no later run
can bring it back.

The manifest names, per station, a `snapshot` and a `review`. Onboarding
registers the station and its bays, imports the dictionary from the snapshot,
applies the review, and - with `--activate` - writes the bindings and opens the
ingestion checkpoint. `.azure/deployment-plan.md` section 14 is the full
procedure, including what must exist first and what still does not work.

## What each file is

| File | What it is |
|---|---|
| `estate.manifest.json` | Station identities and bay keys. Top level may hold `version` and `stations` only. Pins each station's public UUID so it keeps one identity across deployments. |
| `<station>.tags.json` | The tags that station reports, with placeholder values. The dictionary is built from the shape of a snapshot, not from what it measured, so this carries no telemetry. |
| `<station>.review.json` | Every point decided: approved with its catalogue mapping, unit and note, or withheld with its reason. |
| `maple-grove-pump02.review.json` | The record of one investigation - 63 channels reading a constant zero for the whole window. Its decisions are already inside `maple-grove.review.json`; it is kept because its provenance text explains what the bulk pack only states. |

| Station | Source key | Approved | Withheld | Points |
|---|---|---:|---:|---:|
| Cedar Creek Effluent Pump Station | PH_3 | 802 | 103 | 905 |
| Millbrook Influent Pump Station | PH_2 | 452 | 467 | 919 |
| Maple Grove Lift Station | PH_7 | 198 | 173 | 371 |
| **Total** | | **1452** | **743** | **2195** |

The approved count for each station is also its binding count, and therefore
the number of signals a machine page can draw.

## Why these are not a database dump

A dump carries primary keys, and a deployment that already holds other assets
will collide on them. These resolve identity the way the registry does -
`source_entity_uuid` for the station, `source_key` for the bay, part IPN and
component code for the catalogue - so they apply to a database that already has
its own machines, parts and users without touching any of them.

## Why the packs carry no dictionary_hash

`export_dictionary_review` pins one, and it covers each point's status, note,
unit and mapping - the state *after* review. A deployment bootstrapping from a
fresh snapshot has an unreviewed dictionary, so the hash can never match and
the apply is refused with "Dictionary changed; export a fresh review pack."
`machine_health.tests.test_bootstrap_replay` fails if a re-export puts one
back. The per-path lookup still refuses loudly if the target dictionary lacks a
tag a pack names.

## Keeping these honest

`machine_health.tests.test_estate_bootstrap` runs the whole bootstrap against
an empty database and asserts the counts in the table above, that a dry run
writes nothing, and that running it twice changes nothing the second time. If
you re-export any of these files, run that suite before committing.
