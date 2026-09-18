# Developer runbook: rebuild and restart the dev stack

Everything needed to rebuild and restart the local InvenTree/AIMMS stack, including the
Cosmos pumphouse telemetry pieces. Commands are verified against
`contrib/container/dev-docker-compose.yml` and the container's task list.

For *plant configuration* (Azure source, estate manifest, dictionary review, schematic
sign-off) see `contrib/cosmos/HANDOFF.md`. This document only covers building and running.

---

## 0. Shorthand

```bash
cd /Users/Aniketkumar/IdeaProjects/InvenTree
alias dc='docker compose --project-directory . -f contrib/container/dev-docker-compose.yml'
```

> **`--project-directory .` is mandatory.** Every path inside the compose file is relative
> to the repository root. Omit it and Compose fails with
> `env file .../contrib/container/contrib/container/docker.dev.env not found`.

### Services

| Service | Role |
|---|---|
| `inventree-dev-db` | PostgreSQL 17 |
| `inventree-dev-server` | Django server (image `inventree-dev-image`) |
| `inventree-dev-worker` | Django-Q2 worker — runs the pumphouse poller |
| `cosmos-emulator` | Azure Cosmos emulator, **`cosmos` profile only** |

The emulator image is pinned to `vnext-EN20260907`: multi-arch (works on Apple silicon)
and serves plain **HTTP**, not HTTPS. In-network endpoint `http://cosmos-emulator:8081`;
from the host `http://localhost:8081`.

---

## 1. Rebuild the image — only when Python dependencies changed

```bash
dc build inventree-dev-server
```

Application source is bind-mounted, so **code changes need only a restart**, not a rebuild.

Rebuild when `src/backend/requirements.txt` (or the Dockerfile) changes. Skipping this is
what produced a month-stale image missing `azure-cosmos`. That failure hid for weeks because
the connector imports the Azure SDK lazily — the app started fine and the poller with the
flag off never touched it.

---

## 2. Start the stack

```bash
dc --profile cosmos up -d     # with the local Cosmos emulator
# dc up -d                    # without it
```

Without `--profile cosmos` the emulator does not start, by design, so a plain `up` stays light.

---

## 3. Apply migrations

```bash
dc exec inventree-dev-server python src/backend/InvenTree/manage.py migrate
```

The telemetry work needs at least `assets.0014_station_activation`. Check status with:

```bash
dc exec inventree-dev-server invoke int.showmigrations
```

---

## 4. Rebuild the frontend

```bash
dc exec inventree-dev-server invoke int.frontend-compile
```

**If you added or changed any UI string, use `--extract`:**

```bash
dc exec inventree-dev-server invoke int.frontend-compile --extract
```

This re-extracts the lingui catalogs. Without it, lingui prints the message **ID** instead of
the text, so new pages render as hashes such as `JQ875a` or `TS22Ib` rather than labels.

Build inside the container — `yarn` is not installed on the host.

Useful relatives: `int.frontend-install`, `int.frontend-trans`, `int.frontend-build`,
`int.frontend-check`, `dev.frontend-server` (vite dev server with hot reload).

---

## 5. Restart server and worker

```bash
dc restart inventree-dev-server inventree-dev-worker
```

**Restart both.** The worker runs the poller and picks up settings at start. A stale worker
against newer code gives:

```
AttributeError: 'Settings' object has no attribute 'AIMMS_COSMOS_PUMPHOUSE_ENABLED'
```

retried ~5×/minute. It fails *closed* (no unintended reads), which also makes it easy to miss.

---

## 6. Verify

```bash
curl -s -o /dev/null -w 'api %{http_code}\n' http://localhost:8000/api/
curl -s -o /dev/null -w 'web %{http_code}\n' http://localhost:8000/web/
dc logs --tail=40 inventree-dev-worker
```

Expect `200` from both and a clean worker log.

Then **hard-reload the browser (⌘+Shift+R)**. `index.html` is cached, so a freshly built
bundle will not otherwise appear.

Login for the local dev database: `admin` / `inventree`.

---

## 7. Full clean rebuild

```bash
dc down
dc build --no-cache inventree-dev-server
dc --profile cosmos up -d
dc exec inventree-dev-server python src/backend/InvenTree/manage.py migrate
dc exec inventree-dev-server invoke int.frontend-compile --extract
dc restart inventree-dev-server inventree-dev-worker
```

`dc down` **keeps** the database (named volume).

> **Do not add `-v` casually.** `dc down -v` destroys the Postgres volume: station 17, its
> 14 bays, 30 components, 31 dictionary points and 25 bindings all go, meaning HANDOFF §2 and
> §3 must be redone from scratch.

The **emulator's contents do not survive `down`** regardless — re-seed after bringing it up.

---

## 8. Cosmos emulator: seed and keep fresh

```bash
python contrib/cosmos/provision.py          # verify container definition
python contrib/cosmos/seed.py               # write pilot snapshots
```

Both default to `http://localhost:8081` for the emulator (HTTP, not HTTPS — see §0).

The committed samples carry their real **July 2025** timestamps, so within five minutes of
seeding every reading falls outside the 300-second freshness window and reads as stale —
indistinguishable from a broken connector. To keep the same payloads re-based onto the
current clock every 60 seconds:

```bash
nohup sh contrib/cosmos/devtools/keep_emulator_fresh.sh > /tmp/reseed.log 2>&1 < /dev/null & disown
pkill -f keep_emulator_fresh.sh      # stop it
```

It invents no values; it only moves the clock.

By default it rebases `contrib/pump-cassandra/PH_3.full-snapshot.json` — all 845 tags, which
populates all 581 bindings. Set `SNAPSHOT=contrib/cosmos/samples/ph3_snapshots.json` for the
smaller pilot excerpt, but note that the excerpt carries only ~31 tags, so most bindings stay
empty and the trend picker will list parameters that never plot.

> `pkill -f keep_emulator_fresh.sh` will also match a `sh -c` wrapper containing that string —
> including the very command you used to run it, which then kills itself and prints nothing.
> Check with `ps aux | grep keep_emul` from outside, or kill by PID.

> Use `nohup … < /dev/null … & disown`. A bare `&` gets `SIGTTIN`-suspended (process state `TN`).

---

## 9. Run the tests

```bash
dc exec inventree-dev-server python src/backend/InvenTree/manage.py test \
  assets machine_health \
  --testrunner=django.test.runner.DiscoverRunner --noinput --keepdb
```

The explicit `--testrunner` is required — the container does not ship `django_slowtests`.

Frontend checks:

```bash
cd src/frontend
node node_modules/typescript/bin/tsc --noEmit
node node_modules/.bin/playwright test --config playwright.mimic.config.ts
```

The browser suite mocks the API; it needs no backend and no Azure account.

---

## 10. Kill-switch

`AIMMS_COSMOS_PUMPHOUSE_ENABLED` lives in `data/config.yaml` (untracked local config) and is
currently **`True`**.

That is safe while the only configured Health Source is the emulator. It also means **polling
begins the moment the stack comes up**.

> Set it to `false` **before** configuring a real Azure source, then enable it deliberately
> after the HANDOFF §5 checks. Restart the worker after changing it.

---

## Gotchas index

| Symptom | Cause | Fix |
|---|---|---|
| `env file .../contrib/container/contrib/container/docker.dev.env not found` | missing `--project-directory .` | §0 |
| UI shows `JQ875a`, `TS22Ib` instead of labels | lingui catalogs not extracted | §4 with `--extract` |
| New frontend build not visible | `index.html` cached | hard-reload ⌘+Shift+R |
| `module 'azure' has no attribute 'cosmos'` | stale image, deps not installed | §1 rebuild |
| `AttributeError: … AIMMS_COSMOS_PUMPHOUSE_ENABLED` loop | worker older than the code | §5 restart worker |
| `{"detail":"Equipment scope is not configured for this account."}` | container missing `AIMMS_*` env vars | recreate containers (§2/§7) |
| Readings all stale minutes after seeding | sample timestamps are July 2025 | §8 freshness script |
| Emulator unreachable | using `https://`; image serves HTTP | §0 / §8 |
| `ModuleNotFoundError: django_slowtests` | default test runner | §9 `--testrunner=…` |
| Background job suspended, state `TN` | bare `&` got `SIGTTIN` | §8 `nohup … & disown` |
