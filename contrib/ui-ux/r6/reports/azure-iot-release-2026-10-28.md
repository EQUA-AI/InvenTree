# IOT-Merge application release — 28 October 2026

The user authorized committing the current application fixes and deploying the
Azure application. The prior combined demo movie remains outside Git and is
unchanged. No PR or issue is created.

The isolated candidate is based on remote IOT-Merge
`ec1e85c5fb590a1f1b168e3216bb7d83c23628d7`. Three overlapping files merge cleanly
and retain both remote spoken-reference normalization and local authorized
station/history behavior. Source, tests and required documentation are included.
Five local-only replay files are excluded: the command package, replay command,
replay service and its guard test. No production import requires those files.
The original dirty checkout and untracked files are preserved separately.

Included behavior covers native speech review/repair-scope receipts, fresh Work
evidence and guarded citations, Azure provider handling, optional historical
Cosmos reads, test-alarm intake/evidence, repair lifecycle, preview readability,
quiet microphone recovery and timezone-safe completion timestamps. The new
repair-scope authoring path remains a prepared E03 exercise with pending gates
and zero replacement parts. New event-to-work behavior remains false by default.

Fresh candidate frontend checks: 141 unit modules / 1,343 tests pass; TypeScript
passes. Static dependency and merge review passes. Backend tests, pre-commit,
image build, schema lineage and authenticated candidate verification are pending.
No model, migration or dependency manifest changed. This is not deployment or
full product acceptance.

Fresh Azure inventory: aimms-experimental in EpconChat currently serves
7e33232c7d09a79d27fa76e8dcecd84cb2d09e42 at immutable digest
acfee4df1108ca1e37d253796c84f02a93d21e18c53e9f3c01c571abb85e729e.
Multiple revision mode uses one replica and one Gunicorn worker; topology guard
passes. Existing AUTO_UPDATE=False and Cosmos disabled configuration are retained.
Worker inventree-worker read/update access is unavailable to the current Azure
identity; its current image and heartbeat cannot yet be qualified.

Sanitized evidence is outside Git at
`/Users/Aniketkumar/EQUA-AIMMS-Recorder/azure-deploy-20261028/`. It contains the
source preservation manifest, selected-file manifest, overlap review, frontend
logs and sanitized Azure inventory. No secret value is included in this record.

Rollback retains the previously serving immutable image/revision and its traffic
allocation. Migration-on-start must remain disabled. Further rollout results
will be recorded only after actual checks.
