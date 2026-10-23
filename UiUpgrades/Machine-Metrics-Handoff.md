# Machine metrics handoff

Branch: `handoff/machine-metrics`
Base: `a2d4896d38f7e3cae8b74673900fb145d3c2920c`
Repository: `https://github.com/EQUA-AI/InvenTree`

This branch packages the production machine-metrics UI from the original working tree as an independent change. It includes the supporting board-card presentation, health loading/error states, signal-trend geometry, production scope enforcement and regression tests required to preserve the behavior specified in [the implementation plan](Machine-Metrics-Production-UI-Implementation-Plan.md).

The independent chat drawer, conversation focus, OpenUI, voice, attachment and deployment edits from the original working tree are excluded. In particular, the new contextual Ask actions and their conversation-focus regression are excluded from the shared board/work-order files and test harness. Existing chat behavior from the base commit remains available.

## Start working

```sh
git fetch origin
git switch -c improve-machine-metrics origin/handoff/machine-metrics
```

Use this branch as the starting point for subsequent commits. A cherry-pick onto another base may need additional integration work.

## Removal boundary

The deleted session engine, provisioning tools and metrics migration sources were **untracked** in the original checkout and are already absent from this branch's base. They therefore do not appear as tracked deletions in the handoff commit. The branch has no dependency on them. If another checkout contains the old tracked engine, cherry-picking this commit alone will not delete those files; apply the removal inventory in the plan separately or start from this branch.

No live database, schema, migration bookkeeping or deployment was modified. Whether any deployment applied the removed migration sources is unknown. Such a deployment needs a separately reviewed schema-retirement procedure before deploying their removal.

## Verification

Validation passed against this isolated source: 44 focused unit tests, 73 browser/project cases, TypeScript, production build, configured hooks and source-only backend smoke checks. See [implementation notes](UI-Implementation-Notes.md) for results and limits. Browser regressions intercept API responses and do not establish authenticated backend integration. No isolated database is provisioned for the handoff.

Install dependencies using the repository's existing tooling, then run from `src/frontend`:

```sh
yarn install
yarn extract --clean
yarn compile
yarn test:unit src/pages/assets/locations/machineMetrics.test.ts src/pages/assets/locations/locationTree.test.ts src/pages/assets/health/trendGeometry.test.ts src/functions/readQueryPolicy.test.ts
yarn exec tsc --noEmit --pretty false
METRICS_TEST_PORT=5176 yarn exec playwright test --config=playwright.machine-metrics.config.ts
```

The location and maintenance-widget configurations use port 5173 by default; make sure that server serves this branch when running those suites. Historical references to Git-ignored `LocalDocs/` in the original specification are contextual records and are not packaged here.
