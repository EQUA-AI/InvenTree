# Pump-system master catalogue

`pump_systems.json` contains **20 generalized component families and 63 parameter
channels**, derived from the supplied pump hierarchy and tag patterns. It is not
a complete 1,000–1,200-tag pump dictionary and is not an OEM equipment specification.

## What is created

- One **Pump System Master** category root, eight organizational groups and one
  leaf category for each component family (29 categories total).
- One `Part` per family, with stable `PS-...` internal part numbers. There are no
  station names, pump numbers, installed quantities or fabricated serial numbers.
- One `ParameterTemplate` for each supplied channel, named
  `PUMP | <component code> | <measurement>`. Templates target `Part`.
- Empty category-template defaults and empty parameter slots on the family Part.
  These use the existing `Part.copy_category_parameters()` workflow, so a new
  Part created through the normal API in a leaf category can inherit its slots.
- Namespaced metadata (`pump_system_catalogue`) recording provenance, raw tag
  spelling, channel, measurement kind, candidate data type and unit-review status.

These are **catalogue families, not installed assets**. They have `component=True`,
but purchasing, sales, serial tracking and assembly flags are disabled initially.
Curate actual purchasable parts, OEM models and specifications separately. No BOM
or stock is implied. Motor control, motor electrical, excitation and equipment
status groups are virtual Parts because they are logical measurement groupings;
the other entries represent physical component/structure families.

Location/ownership remains `unassigned`. A family may later be installed at a
pump or pumphouse, but that future association must not change the catalogue
identity. Shared Infrastructure is a browsing category, not a fixed site owner.
`PUMP1`…`PUMP14` identify future instances, not 14 duplicate catalogue parts.

## Engineering review, not fabricated values

The manifest is based on user-supplied descriptions, **not externally verified
manufacturer data**. No thresholds, safe operating ranges, capacities, material
grades, vendor identities or live readings are seeded. Earlier illustrative
temperature thresholds are deliberately not used.

| Unit status | Meaning |
|---|---|
| `proposed` | Conventional candidate unit; must be checked against source documentation. |
| `unresolved` | Measurement semantics or supported unit definition is unknown; units are blank. |
| `unitless` | Status or ratio concept; units are blank, but encoding/scaling still needs review. |

The installed application's unit registry accepts the proposed spellings `degC`,
`bar`, `rpm`, `kW`, `A`, `V`, `Hz`, `percent` and `m`. Acceptance validates syntax,
**not actual SCADA units**. Examples of remaining review:

- RTD channels: confirm temperature versus raw resistance, and Celsius scaling.
- Pressure: distinguish gauge and absolute; level: confirm datum and depth/elevation.
- Vibration: displacement, velocity or acceleration; RMS/peak and sensor axes.
- Spiral-case and pad suffixes: the tag name alone does not identify the quantity.
- Reactive power: confirm var/kvar/Mvar. The checked registry does not recognize
  `var`/`kvar`; leave it unresolved pending a reviewed custom-unit definition,
  rather than substituting active-power units.
- Voltage/power: confirm V/kV and W/kW/MW; valve position: percent/fraction/travel.
- `st`: pump/station ownership and status encoding are unknown.
- Preserve typos, spaces and suffixes such as `PUMP_POWERFATCOR`,
  `COMMAN_FORBAY_LEVEL`, `PUMP_MOTOR_COLD_AIR TEMP1` and `D5`/`D6` until the real
  source dictionary establishes their meaning. A possible alias is not merged.

Blank slots mean **not populated**, never zero, OFF or healthy. They are catalogue
definitions, not observations. Status templates deliberately are not checkboxes,
because saving an empty checkbox can coerce unknown to false. Existing InvenTree
validation may reject individually editing an empty numeric parameter; the
category-copy workflow intentionally supports empty defaults. Do not disable
unit validation globally. Do not write five-second readings onto shared Part
parameters: future bindings need the identity of the installed component.

## Run locally

Prerequisite: the project's Docker development server is already running and its
normal dependencies/migrations are installed. This command uses existing Django
and InvenTree dependencies; **no new packages or schema migrations are needed**.

From the repository root, preview first:

```zsh
docker compose --project-directory . -f contrib/container/dev-docker-compose.yml exec -T inventree-dev-server python3 src/backend/InvenTree/manage.py load_pump_catalogue --dry-run
```

Apply:

```zsh
docker compose --project-directory . -f contrib/container/dev-docker-compose.yml exec -T inventree-dev-server python3 src/backend/InvenTree/manage.py load_pump_catalogue
```

Alternatively, in an activated non-container backend environment:

```zsh
python3 src/backend/InvenTree/manage.py load_pump_catalogue --dry-run
python3 src/backend/InvenTree/manage.py load_pump_catalogue
```

Browse the Parts catalogue under **Pump System Master**, or search for IPNs
starting with `PS-`. Open a Part's Parameters to review the blank channels;
category parameter templates are reusable for future catalogue entries. Detailed
review metadata is available through the existing metadata API; this change does
not add a custom review UI, live Data tab or SVG diagram.

### Filter by component group and onboarding date

Every family imported by this command receives the Part tag
**`pumphouse-components`**. This is a catalogue classification, not a SCADA signal
tag or assignment to an installed pumphouse. Rerunning the loader backfills the
tag on existing owned Parts, preserving other tags and their original creation
dates. Add the same tag manually when creating related Parts outside this loader.

In the Parts table:

1. Open **Filters → Add Filter → Tags**, select `pumphouse-components`, and apply.
2. Add **Created After** and/or **Created Before** to narrow the result by date.
3. Use the sortable **Creation Date** column to see when each record was onboarded.
   If it is hidden in your saved layout, enable it in the column selector.
4. For more specific component families, navigate into **Pump System Master** and
   its child categories. Keep **Include Subcategories** enabled at a group/root.

These filters combine. Creation Date is when the Part record was first created
in InvenTree, not an installation date or a separate historical onboarding field.
Existing API semantics are exclusive: **Created After September 9** and
**Created Before September 11** select September 10. Unknown creation dates are
excluded when a date bound is applied. Clearing the date bounds includes them
again. Filter selections can be saved using the existing **Save Filters** action.

## Import safety and extension

- All catalogue writes are transactional. `--dry-run` rolls back the entire preview.
- Repeat runs preserve primary keys, existing parameter values/notes, descriptions
  and unrelated metadata. Unchanged templates are not saved again.
- Matching records must carry this loader's ownership marker. Case-insensitive
  IPN, category and template collisions abort instead of adopting unrelated data.
- Semantic conflicts (including unit changes) require an explicit reviewed change;
  the loader never silently converts values or takes over a different definition.
- Adding a component or channel creates only missing records and slots. Removing
  entries does not delete existing data. There is no prune/flush/reset option.
- Run one import at a time; Part IPNs are not database-unique in all deployments.
- `--file /absolute/path/to/catalogue.json` loads a reviewed manifest of the same
  schema; paths must be accessible inside the container when using Docker.
- Component `code` values are stable uppercase identifiers; `channels: [1, 6]`
  expands an **inclusive** range using `{channel}` in name and tag. This is not
  an arbitrary list or a pump-number expansion. Unknown keys, duplicate tags,
  ambiguous ownership and unsupported placeholders are rejected.

No clients, assets, installations, stock, sources, signal bindings, readings,
alarms, work orders or Cassandra/Cosmos connections are created. **Do not run
`dev.setup-test` to import this catalogue**: that task resets demo data.

## Tests

Tests use Django's isolated test database and retain it with `--keepdb`:

```zsh
docker compose --project-directory . -f contrib/container/dev-docker-compose.yml exec -T inventree-dev-server bash -c 'cd /home/inventree/src/backend/InvenTree && "$INVENTREE_PY_ENV/bin/python" -u manage.py test part.test_pump_catalogue part.test_param --keepdb --noinput'
```

Coverage includes idempotence, empty slots, rollback, unit validation, naming
collisions, human-data preservation, additive channels, classification backfills,
combined date/category/tag filters and no equipment/telemetry side effects.
Connector and asset-hierarchy work is deliberately deferred.
