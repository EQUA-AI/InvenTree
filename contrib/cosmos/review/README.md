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

## Order of operations on a fresh deployment

    python manage.py load_pump_catalogue
    python manage.py onboard_pumphouse_estate contrib/pump-cassandra/estate.json --source <pk> --dry-run
    python manage.py apply_dictionary_review --review contrib/cosmos/review/<station>.approvals.review.json --dry-run
    python manage.py apply_dictionary_review --review contrib/cosmos/review/<station>.withheld.review.json --dry-run

Drop `--dry-run` once the counts match the table below. The approvals pack must
go first: it is what creates the bindings, and applying the withheld pack alone
would leave a station looking reviewed but drawing nothing.

Neither pack carries a credential. Create the `HealthSource` on the target with
its own Entra identity, then run `discover_data_range` so `read_ceiling` has a
window to clamp to - without it the connector reads to the wall clock.

## What each pack contains

| Station | Source key | Approved | Withheld | Points |
|---|---|---:|---:|---:|
| Cedar Creek Effluent Pump Station | PH_3 | 802 | 103 | 905 |
| Millbrook Influent Pump Station | PH_2 | 452 | 467 | 919 |
| Maple Grove Lift Station | PH_7 | 198 | 173 | 371 |
| **Total** | | **1452** | **743** | **2195** |

The approved count for each station is also its binding count, and therefore the
number of signals a machine page can draw.

## Why the withheld packs exist separately

`export_dictionary_review` writes unapproved points under `pending`, and
`apply_dictionary_review` reads only `approve` and `withhold`. Replaying an
export as-is would therefore land a deployment with 743 points carrying no
reason at all, looking like a review nobody had started - when in fact every one
of them has a recorded finding behind it, several of them substantial. The
withheld packs carry those reasons across.

`maple-grove-pump02.review.json` is the record of one such investigation - 63
channels reading a constant zero for the whole recorded window. Its entries are
included in `maple-grove.withheld.review.json`, so it does not need applying
separately; it is kept because its provenance and policy text explain a decision
the bulk pack only states.
