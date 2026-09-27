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
    python manage.py onboard_pumphouse_estate contrib/cosmos/review/estate.manifest.json --source <pk> --dry-run
    python manage.py apply_dictionary_review --review contrib/cosmos/review/<station>.approvals.review.json --dry-run
    python manage.py apply_dictionary_review --review contrib/cosmos/review/<station>.withheld.review.json --dry-run

Drop `--dry-run` once the counts match the table below. The approvals pack must
go first: it is what creates the bindings, and applying the withheld pack alone
would leave a station looking reviewed but drawing nothing.

`estate.manifest.json` is the manifest `onboard_pumphouse_estate` accepts, and
it is not `contrib/pump-cassandra/estate.json` - that file carries commentary
keys the onboarder rejects outright ("Manifest requires version 1 and stations
only"), and it is documentation of the estate rather than an input to it. This
one is generated from the registered stations' own identities and pins their
public UUIDs, so a station keeps the same identity across deployments;
`register_station` is idempotent and will return an existing registration
rather than duplicate it.

These files ship inside the production image (see `contrib/container/
Dockerfile`, production stage), so the commands above run as-is in a deployed
container - there is nothing to copy in first.

## What does not work yet on a fresh deployment

Read `.azure/deployment-plan.md` section 14 before relying on the sequence
above. Verified in a clean-room test database, two of its steps are blocked as
shipped:

- The review packs *update* dictionary points; nothing in the production image
  *creates* them. `import_dictionary` needs a parsed snapshot payload per
  station, the manifest supports referencing one through a `snapshot` key, and
  `estate.manifest.json` does not carry it.
- The approvals packs pin a `dictionary_hash` taken after review, and that hash
  covers each point's status, note, unit and mapping. A freshly imported
  dictionary is unreviewed, so it cannot match and the apply is refused with
  "Dictionary changed; export a fresh review pack." The withheld packs carry no
  hash and are unaffected.

Both are recorded with their remedies in section 14.5. The packs are correct
for the database they were taken from - they replay cleanly against it - so
nothing here needs re-deciding, only re-packaging.

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
