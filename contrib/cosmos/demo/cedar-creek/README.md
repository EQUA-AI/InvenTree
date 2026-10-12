# Cedar Creek UI demo dataset

Import Sequence step 18: "Publish single portable demo dataset — one versioned
manifest; no runtime dependency on Download/Deprecated caches; reproducible tested
Cedar Creek UI deployment."

| File | Role |
|---|---|
| `manifest.json` | The versioned definition: pinned workbook hash, population arguments, expected counts, what is deliberately left out |
| `build.sh` | Builds the dataset from an empty database and tests it |
| `verify_build.py` | Compares the build with the manifest and writes `build-report.json` |
| `restore_check.sh` | Restores a built dump into a separate empty Postgres and counts key rows |

## First build (2026-10-12, version `2026-10-12.1`)

`BUILD OK`: all 22 manifest checks and 22/22 QA gates passed, the replay created nothing,
and `restore_check.sh` restored the 1.4 MB dump into a clean pg16 with the same counts
(45 locations, 63 machines, 238 components, 224 part links, 304 parts, 132 history,
18 work orders, 6 stock items). From empty database to dump took about 2 minutes
(migrate ~80 s, apply ~10 s).

## Build

```bash
contrib/cosmos/demo/cedar-creek/build.sh /path/CEDAR_CREEK_AIMMS_UI_POPULATION_MASTER_2026-10-08.xlsx [--keep] [--force]
```

The build:

1. refuses a workbook whose sha256 differs from the manifest;
2. starts `demo-pg` (pgvector pg16) and `demo-app` (`aimms-merged-image`) on their own
   network `demonet`; the `pop-test` sandbox and Azure are not touched;
3. migrates an empty database and creates the `demo-admin` superuser (random password,
   kept in the output folder, mode 600);
4. runs `populate_from_workbook` plan, apply, then apply again, which must create nothing;
5. runs `contrib/cosmos/devtools/cedar_creek_qa_gates.py` (expects 22 pass / 0 fail);
6. writes `pg_dump -Fc` and checks every count against the manifest.

Output goes to `data/demo/cedar-creek/<version>/` (git-ignored):
`cedar-creek-demo.dump`, `build-report.json`, `plan.json`, `apply.json`, `replay.json`,
`qa-gates.txt`, `SHA256SUMS`, `admin-password.txt`, a copy of the workbook and the logs.
A dump's bytes differ between builds (timestamps); `build-report.json` records the hash
of the one produced.

`--keep` leaves the containers running and serves the UI on `http://localhost:8012`
(`DEMO_PORT` to change). Remove them with `docker rm -f demo-app demo-pg && docker network rm demonet`.

## Restore elsewhere

Into an empty PostgreSQL 16+ database used by the same code revision (the report records
`git_sha`):

```bash
pg_restore --no-owner --no-acl -d <database> cedar-creek-demo.dump
```

Run the app with `AIMMS_MAINTENANCE_SCOPE_RESOLVER=tasks.scope.granted_client_scope_resolver`
and `AIMMS_SINGLE_SITE_CLIENT_CODE=cedar-creek-regional-water` so ungranted users see
the Cedar Creek client. Log in as `demo-admin` and change the password.

## Scope

The dataset is the workbook population only. It is a fresh registration (station UUID
`d0b4aace-…`, namespace `cedar-creek-demo`, 17 `CC-REF` components per pump), not a copy
of the Azure database, which adopted the live station 93 instead. Telemetry, staff
accounts, PM plans, attachments, repair packets and compliance screens are not included;
`manifest.json` lists why. The workbook itself is customer package material and is not
committed.

To change the dataset, bump `version` in `manifest.json` and rebuild.
