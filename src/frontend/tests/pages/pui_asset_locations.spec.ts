/**
 * Physical location workspace + machine return-route UI acceptance
 * (packages C, D and machine E of the approved plan: M1–M3, U2).
 *
 * SCOPE: MOCKED BROWSER RENDERING — real components (LocationWorkspace,
 * MachineDetail), real React Router navigation and real React Query caching,
 * every API response supplied by Playwright route mocks. NOT a real backend
 * E2E run: no Django, no database, no auth. The isolated harness lives in
 * playwright/machine-locations.{html,tsx} and is run by the standalone
 * playwright.machine-locations.config.ts (no webServer/globalSetup — it reuses
 * the already-running dev server on :5173).
 *
 * Run: node node_modules/@playwright/test/cli.js test \
 *   --config=playwright.machine-locations.config.ts
 */
import { type Page, expect, test } from '@playwright/test';

const SESSION_ID = '123e4567-e89b-12d3-a456-426614174000';

/*
 * Location fixture: 13 root rows (root overview paginates at 10), a deep
 * branch under Building B whose tail (Zenith Cell, pk 150) is only reachable
 * past the first 100-row branch page, and a leaf Line One (pk 21) used for
 * the pagination/reset scenario.
 */
interface Loc {
  pk: number;
  name: string;
  code: string;
  kind: string;
  parent: number | null;
  client: number;
  archived?: boolean;
  hasChildren?: boolean;
  hidden?: boolean;
}

function fillerChildren(
  parent: number,
  client: number,
  count: number,
  from: number
): Loc[] {
  return Array.from({ length: count }, (_, i) => {
    const n = from + i;
    return {
      pk: 200 + n,
      name: `Shelf ${String(n).padStart(2, '0')}`,
      code: `SH-${String(n).padStart(2, '0')}`,
      kind: 'other',
      parent,
      client
    };
  });
}

const LOCATIONS: Loc[] = [
  {
    pk: 1,
    name: 'North Site',
    code: 'NS',
    kind: 'site',
    parent: null,
    client: 1,
    hasChildren: true
  },
  {
    pk: 2,
    name: 'South Site',
    code: 'SS',
    kind: 'site',
    parent: null,
    client: 1,
    hasChildren: true
  },
  {
    pk: 3,
    name: 'Warehouse',
    code: 'WH',
    kind: 'facility',
    parent: null,
    client: 2
  },
  ...Array.from({ length: 10 }, (_, i) => ({
    pk: 51 + i,
    name: `Depot ${i + 1}`,
    code: `D${i + 1}`,
    kind: 'other' as string,
    parent: null,
    client: 1
  })),
  {
    pk: 11,
    name: 'Building B',
    code: 'BB',
    kind: 'building',
    parent: 1,
    client: 1,
    hasChildren: true
  },
  {
    pk: 12,
    name: 'Area C',
    code: 'AREA-C',
    kind: 'area',
    parent: 11,
    client: 1
  },
  {
    pk: 13,
    name: 'Area D',
    code: 'AREA-D',
    kind: 'area',
    parent: 11,
    client: 1
  },
  ...fillerChildren(11, 1, 100, 1),
  // Sorted by name this row sits past the first 100 children of Building B.
  {
    pk: 150,
    name: 'Zenith Cell',
    code: 'ZC',
    kind: 'cell',
    parent: 11,
    client: 1
  },
  // Resolves as a detail record but is deliberately absent from every branch
  // listing: its path edge can never be loaded from the server.
  {
    pk: 999,
    name: 'Ghost Cell',
    code: 'GC',
    kind: 'cell',
    parent: 11,
    client: 1,
    hidden: true
  },
  { pk: 21, name: 'Line One', code: 'L1', kind: 'line', parent: 2, client: 1 }
];

const BY_PK = new Map(LOCATIONS.map((row) => [row.pk, row]));

function locationRow(loc: Loc) {
  const path: { pk: number; name: string }[] = [];
  for (
    let row: Loc | undefined = loc;
    row;
    row = row.parent ? BY_PK.get(row.parent) : undefined
  ) {
    path.unshift({ pk: row.pk, name: row.name });
  }
  return {
    pk: loc.pk,
    client: loc.client,
    parent: loc.parent,
    name: loc.name,
    code: loc.code,
    kind: loc.kind,
    description: '',
    timezone: 'UTC',
    effective_timezone: 'UTC',
    archived: !!loc.archived,
    version: 1,
    path,
    has_children: !!loc.hasChildren
  };
}

function machineRow(
  pk: number,
  name: string,
  opts: { locationPk?: number | null; active?: boolean; legacy?: string } = {}
) {
  const loc = opts.locationPk ? BY_PK.get(opts.locationPk) : undefined;
  return {
    pk,
    name,
    serial: `S-${pk}`,
    active: opts.active ?? true,
    manufacturer: 'EQUA',
    model: 'Demo',
    location: opts.legacy ?? '',
    description: '',
    physical_location: loc ? locationRow(loc) : null,
    placement_version: 1
  };
}

/* Full-population and demo-cohort totals deliberately differ (plan §M3). */
function countsFor(pk: number, session: string | null) {
  if (pk === 21) {
    return session
      ? {
          direct_machines: 3,
          total_machines: 3,
          direct_open_work_orders: 0,
          total_open_work_orders: 1
        }
      : {
          direct_machines: 30,
          total_machines: 30,
          direct_open_work_orders: 1,
          total_open_work_orders: 2
        };
  }
  return session
    ? {
        direct_machines: 2,
        total_machines: 3,
        direct_open_work_orders: 1,
        total_open_work_orders: 2
      }
    : {
        direct_machines: 5,
        total_machines: 9,
        direct_open_work_orders: 2,
        total_open_work_orders: 4
      };
}

interface RecordedRequest {
  path: string;
  params: URLSearchParams;
}

let machinesError = false;
let locationsError = false;

async function installMocks(page: Page) {
  const listRequests: RecordedRequest[] = [];
  const detailRequests: RecordedRequest[] = [];
  const machineListRequests: RecordedRequest[] = [];

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const params = url.searchParams;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, json: body as any });

    if (path.endsWith('/locations/context/')) {
      return json({
        workspaces: [
          { pk: 1, name: 'North client' },
          { pk: 2, name: 'South client' }
        ],
        can_add: true,
        can_change: true
      });
    }

    const detailMatch = path.match(/\/locations\/(\d+)\/$/);
    if (detailMatch) {
      detailRequests.push({ path, params });
      const loc = BY_PK.get(Number(detailMatch[1]));
      if (!loc) return json({ detail: 'Not found' }, 404);
      return json({
        ...locationRow(loc),
        counts: countsFor(loc.pk, params.get('demo_session'))
      });
    }

    if (path.endsWith('/locations/machines/')) {
      machineListRequests.push({ path, params });
      if (machinesError) return json({ error: 'SERVER_ERROR' }, 500);

      const search = params.get('search') ?? '';
      const session = params.get('demo_session');
      const locationPk = params.get('location')
        ? Number(params.get('location'))
        : null;
      const machinePk = params.get('machine')
        ? Number(params.get('machine'))
        : null;
      const unassigned = params.get('unassigned') === 'true';
      const includeDescendants = params.get('include_descendants') !== 'false';
      const limit = Number(params.get('limit') ?? '25');
      const offset = Number(params.get('offset') ?? '0');

      if (machinePk) {
        return json({
          count: 1,
          results: [
            machineRow(machinePk, `Pump ${machinePk}`, {
              locationPk: machinePk === 6 ? null : 12
            })
          ],
          next: null
        });
      }
      if (search) return json({ count: 0, results: [], next: null });
      if (unassigned) {
        return json({
          count: 1,
          results: [
            machineRow(6, 'Pump 2', {
              locationPk: null,
              active: false,
              legacy: 'Old line 3'
            })
          ],
          next: null
        });
      }
      let rows: any[] = [];
      let count = 0;
      if (locationPk === 21) {
        count = session ? 3 : 30;
        rows = Array.from({ length: count }, (_, i) =>
          machineRow(60 + i, `Fan ${i + 1}`, { locationPk: 21 })
        );
      } else if (locationPk) {
        // Reconciles with countsFor(): table count equals the summary card.
        count = includeDescendants ? (session ? 3 : 9) : session ? 2 : 5;
        rows = [
          machineRow(5, 'Pump 1', { locationPk: 12 }),
          machineRow(6, 'Pump 2', {
            locationPk: null,
            active: false,
            legacy: 'Old line 3'
          }),
          machineRow(7, 'Pump 3', { locationPk: 12 }),
          ...Array.from({ length: 6 }, (_, i) =>
            machineRow(30 + i, `Compressor ${i + 1}`, { locationPk: 12 })
          )
        ];
      } else {
        count = 12;
        rows = [
          machineRow(5, 'Pump 1', { locationPk: 12 }),
          machineRow(6, 'Pump 2', {
            locationPk: null,
            active: false,
            legacy: 'Old line 3'
          }),
          ...Array.from({ length: 10 }, (_, i) =>
            machineRow(40 + i, `Grinder ${i + 1}`, { locationPk: 12 })
          )
        ];
      }
      return json({
        count,
        next: offset + limit < count ? 'next' : null,
        results: rows.slice(offset, offset + limit)
      });
    }

    if (path.endsWith('/locations/')) {
      listRequests.push({ path, params });
      if (locationsError) return json({ error: 'SERVER_ERROR' }, 500);
      const parent = params.get('parent');
      const search = (params.get('search') ?? '').toLowerCase();
      const limit = Number(params.get('limit') ?? '25');
      const offset = Number(params.get('offset') ?? '0');
      let rows = LOCATIONS.filter((row) => !row.hidden);
      if (parent != null) {
        const pk = parent === 'root' ? null : Number(parent);
        rows = rows.filter((row) => row.parent === pk);
      }
      if (search) {
        rows = rows.filter(
          (row) =>
            row.name.toLowerCase().includes(search) ||
            row.code.toLowerCase().includes(search)
        );
      }
      rows = [...rows].sort(
        (a, b) =>
          a.name.toLowerCase().localeCompare(b.name.toLowerCase()) ||
          a.pk - b.pk
      );
      return json({
        count: rows.length,
        next: offset + limit < rows.length ? 'next' : null,
        results: rows.slice(offset, offset + limit).map(locationRow)
      });
    }

    const machineMatch = path.match(/\/assets\/machines\/(\d+)\/$/);
    if (machineMatch) {
      return json({
        ...machineRow(Number(machineMatch[1]), `Pump ${machineMatch[1]}`, {
          locationPk: Number(machineMatch[1]) === 6 ? null : 12,
          active: Number(machineMatch[1]) !== 6,
          legacy: Number(machineMatch[1]) === 6 ? 'Old line 3' : ''
        }),
        barcode_hash: ''
      });
    }

    if (path.includes('/demo-metrics/')) {
      if (path.endsWith('/sessions/')) {
        return json({
          count: 1,
          results: [
            {
              id: SESSION_ID,
              dataset_key: 'equa-demo-metrics-v1',
              session_key: 'equa-demo-1',
              mode: 'bounded_current',
              status: 'active',
              synthetic: true,
              anchor_at: null,
              expires_at: null
            }
          ]
        });
      }
      if (path.endsWith('/metrics/')) {
        return json({
          mode: 'bounded_current',
          synthetic: true,
          session: {
            id: SESSION_ID,
            dataset_key: 'equa-demo-metrics-v1',
            session_key: 'equa-demo-1',
            status: 'active',
            anchor_at: null,
            expires_at: null
          },
          generated_at: '2026-09-26T12:00:00Z',
          filters: { location_id: 12, include_descendants: true },
          cohort_size: 2,
          machines: [
            {
              alias: 'pump-1',
              machine_id: 5,
              coverage_state: 'fresh',
              condition: 'normal',
              required_signals: ['temp'],
              present_signals: ['temp'],
              fresh_signals: ['temp'],
              last_observed_at: '2026-09-26T11:00:00Z'
            }
          ],
          observation_coverage: {
            fresh: 1,
            partial: 0,
            stale: 0,
            never_seen: 0,
            not_configured: 0
          },
          fresh_condition: { normal: 1, warning: 0, critical: 0 },
          open_work_orders: 1,
          machines_with_open_work: 1,
          overdue_open_work_orders: 0,
          open_by_state: { backlog: 1 },
          capabilities: {
            history_available: true,
            replay_available: false,
            anomaly_creation: false,
            oee: false
          }
        });
      }
      if (path.endsWith('/work-orders/')) {
        return json({
          count: 1,
          open_count: 1,
          results_returned: 0,
          has_more: false,
          filters: { location_id: 12, include_descendants: true },
          session: {
            id: SESSION_ID,
            session_key: 'equa-demo-1',
            synthetic: true
          },
          results: []
        });
      }
      if (path.endsWith('/history/')) {
        return json({
          available: false,
          synthetic: true,
          reason: 'no_imported_history_for_session',
          filters: { location_id: 12, include_descendants: true },
          selected_machines: 0,
          measured_machines: 0,
          completeness: 'none',
          daily: [],
          planned_machine_minutes: 0,
          downtime_machine_minutes: null,
          measured_cohort_availability: null
        });
      }
    }

    return json({});
  });

  return { listRequests, detailRequests, machineListRequests };
}

function lastMachineRequest(requests: RecordedRequest[]) {
  return requests[requests.length - 1];
}

test.beforeEach(() => {
  machinesError = false;
  locationsError = false;
});

test('chevron expands without selecting; label selects without collapsing', async ({
  page
}) => {
  await installMocks(page);
  // South sibling selected first; North Site is a collapsed parent.
  await page.goto(
    '/playwright/machine-locations.html?mode=workspace&location=3'
  );
  await expect(
    page.locator('[role="treeitem"][data-value="3"]')
  ).toHaveAttribute('aria-selected', 'true');

  await page.getByRole('button', { name: 'Expand North Site' }).click();
  await expect(
    page.getByRole('button', { name: 'Collapse North Site' })
  ).toBeVisible();
  // Expansion must not change the selection.
  await expect(
    page.locator('[role="treeitem"][data-value="3"]')
  ).toHaveAttribute('aria-selected', 'true');
  await expect(
    page.locator('[role="treeitem"][data-value="1"]')
  ).toHaveAttribute('aria-selected', 'false');
  await expect(
    page.getByRole('button', { name: 'Building B', exact: true })
  ).toBeVisible();

  // Expand Building B first so "selecting does not collapse" is observable.
  await page.getByRole('button', { name: 'Expand Building B' }).click();
  await expect(
    page.getByRole('button', { name: 'Area C', exact: true })
  ).toBeVisible();

  // Selecting the label changes the selection and must not collapse the branch.
  await page.getByRole('button', { name: 'Building B', exact: true }).click();
  await expect(
    page.locator('[role="treeitem"][data-value="11"]')
  ).toHaveAttribute('aria-selected', 'true');
  await expect(
    page.getByRole('button', { name: 'Area C', exact: true })
  ).toBeVisible();
});

test('tree rows show type labels, codes, and chevrons only for branches', async ({
  page
}) => {
  await installMocks(page);
  await page.goto('/playwright/machine-locations.html?mode=workspace');
  const tree = page.getByRole('tree', { name: 'Location tree' });
  await expect(tree).toBeVisible();

  const northSite = tree.locator('[role="treeitem"][data-value="1"]');
  await expect(
    northSite.getByText('North Site', { exact: true })
  ).toBeVisible();
  await expect(northSite.getByText('Site', { exact: true })).toBeVisible();
  await expect(northSite.getByText('NS', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Expand North Site' })
  ).toBeVisible();

  // Leaves never render a chevron; the label stays a plain selection target.
  const warehouse = tree.locator('[role="treeitem"][data-value="3"]');
  await expect(warehouse.getByText('Facility', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('button', { name: /Expand Warehouse/ })
  ).toHaveCount(0);
});

test('keyboard: Enter selects the focused node; Home/End and type-ahead move focus', async ({
  page
}) => {
  await installMocks(page);
  await page.goto('/playwright/machine-locations.html?mode=workspace');

  const southSite = page.locator('[role="treeitem"][data-value="2"]');
  await southSite.focus();
  await page.keyboard.press('Enter');
  await expect(southSite).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByTestId('destination')).toContainText('location=2');

  const first = page.locator('[role="treeitem"]').first();
  await first.focus();
  await page.keyboard.press('End');
  await expect
    .poll(() =>
      page.evaluate(() => {
        const active = document.activeElement;
        return active?.getAttribute('role') === 'treeitem'
          ? (active.getAttribute('data-value') ?? '')
          : 'not-a-treeitem';
      })
    )
    .not.toBe('not-a-treeitem');
  const endValue = await page.evaluate(() =>
    document.activeElement?.getAttribute('data-value')
  );
  await page.keyboard.press('Home');
  await expect
    .poll(() =>
      page.evaluate(
        () => document.activeElement?.getAttribute('data-value') ?? ''
      )
    )
    .toBe(await first.getAttribute('data-value'));
  expect(endValue).not.toBe(await first.getAttribute('data-value'));

  // Type-ahead focuses the first node whose name starts with the typed text.
  await page.keyboard.press('w');
  await expect
    .poll(() => page.evaluate(() => document.activeElement?.textContent ?? ''))
    .toContain('Warehouse');
});

test('branch pagination: Load more locations continues the branch', async ({
  page
}) => {
  await installMocks(page);
  await page.goto('/playwright/machine-locations.html?mode=workspace');
  await page.getByRole('button', { name: 'Expand North Site' }).click();
  await page.getByRole('button', { name: 'Expand Building B' }).click();
  await expect(
    page.getByRole('button', { name: 'Area C', exact: true })
  ).toBeVisible();
  // Zenith Cell is past the first 100-row branch page.
  await expect(
    page.getByRole('button', { name: 'Zenith Cell', exact: true })
  ).toHaveCount(0);

  const loadMore = page.getByRole('button', { name: /Load more locations/ });
  await expect(loadMore).toBeVisible();
  await loadMore.click();
  await expect(
    page.getByRole('button', { name: 'Zenith Cell', exact: true })
  ).toBeVisible({ timeout: 15_000 });
});

test('deep link beyond the first branch page is revealed in the tree', async ({
  page
}) => {
  await installMocks(page);
  await page.goto(
    '/playwright/machine-locations.html?mode=workspace&location=150'
  );
  await expect(
    page.getByRole('heading', { name: 'Zenith Cell' })
  ).toBeVisible();
  const revealed = page.locator('[role="treeitem"][data-value="150"]');
  // Reveal pages through a 100-row branch; allow for dev-server latency.
  await expect(revealed).toBeVisible({ timeout: 15_000 });
  await expect(revealed).toHaveAttribute('aria-selected', 'true');
  // The revealed path is expanded: its parent is visible and open.
  await expect(
    page.getByRole('button', { name: 'Collapse Building B' })
  ).toBeVisible();
});

test('root overview: Browse top-level locations lists roots when nothing is selected', async ({
  page
}) => {
  const { listRequests } = await installMocks(page);
  await page.goto('/playwright/machine-locations.html?mode=workspace');
  await expect(page.getByText('Choose a location')).toHaveCount(0);
  await expect(
    page.getByRole('heading', { name: 'Browse top-level locations' })
  ).toBeVisible();
  // Root rows are real navigation links; the overview paginates at 10 and the
  // first page holds the sorted Depots.
  await expect(
    page.getByRole('link', { name: 'Depot 1', exact: true })
  ).toBeVisible();
  await expect(page.getByRole('link', { name: 'Warehouse' })).toHaveCount(0);

  await page
    .getByRole('navigation', { name: /pagination/i })
    .getByRole('button', { name: '2' })
    .click();
  await expect(page.getByRole('link', { name: 'Warehouse' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'North Site' })).toBeVisible();
  await expect
    .poll(() => {
      const root = listRequests.filter(
        (r) => r.params.get('parent') === 'root'
      );
      return root.some((r) => r.params.get('offset') === '10');
    })
    .toBe(true);
});

test('Include sublocations toggle drives table scope and wording', async ({
  page
}) => {
  const { machineListRequests } = await installMocks(page);
  await page.goto(
    '/playwright/machine-locations.html?mode=workspace&location=12'
  );
  const toggle = page.getByRole('switch', { name: 'Include sublocations' });
  await expect(toggle).toBeChecked();
  await expect(
    page.getByText(
      'Includes machines placed at this location and all of its sublocations.'
    )
  ).toBeVisible();
  await expect(page.getByText('Including sublocations')).toBeVisible();

  await toggle.uncheck();
  await expect(page.getByTestId('destination')).toContainText('scope=direct');
  await expect(
    page.getByText('Includes only machines placed directly at this location.')
  ).toBeVisible();
  await expect(page.getByText('Direct here')).toBeVisible();
  await expect
    .poll(() => {
      const req = lastMachineRequest(machineListRequests);
      return req?.params.get('include_descendants');
    })
    .toBe('false');
});

test('demo cohort reconciles summary and rows; both requests carry the session', async ({
  page
}) => {
  const { detailRequests, machineListRequests } = await installMocks(page);
  await page.goto(
    '/playwright/machine-locations.html?mode=workspace&location=12'
  );
  await expect(page.getByTestId('machine-count-view')).toHaveText('9');
  await expect(page.getByText('9 machines')).toBeVisible();
  await expect(
    page.getByRole('columnheader', { name: 'Active record' })
  ).toBeVisible();
  // No-demo default: nothing carries a demo session.
  expect(machineListRequests.every((r) => !r.params.get('demo_session'))).toBe(
    true
  );

  await page.evaluate(
    (sessionId) =>
      (window as any).__machineTest.setSearch('demo_session', sessionId),
    SESSION_ID
  );

  // RED/GREEN contract: BOTH the detail summary and the machine list carry
  // the active demo session, and the cards reconcile with the narrowed rows.
  await expect
    .poll(() =>
      detailRequests.some((r) => r.params.get('demo_session') === SESSION_ID)
    )
    .toBe(true);
  await expect
    .poll(() =>
      machineListRequests.some(
        (r) => r.params.get('demo_session') === SESSION_ID
      )
    )
    .toBe(true);
  await expect(page.getByTestId('machine-count-view')).toHaveText('3');
  await expect(page.getByText('3 machines')).toBeVisible();
});

test('cohort change resets table page and selection', async ({ page }) => {
  const { machineListRequests } = await installMocks(page);
  await page.goto(
    '/playwright/machine-locations.html?mode=workspace&location=21'
  );
  await expect(page.getByText('30 machines')).toBeVisible();

  await page
    .getByRole('navigation', { name: /pagination/i })
    .getByRole('button', { name: '2' })
    .click();
  await expect
    .poll(() => lastMachineRequest(machineListRequests)?.params.get('offset'))
    .toBe('25');

  await page.getByRole('checkbox', { name: 'Select Fan 26' }).check();
  await expect(page.getByText(/Selected on this page/)).toBeVisible();

  await page.evaluate(
    (sessionId) =>
      (window as any).__machineTest.setSearch('demo_session', sessionId),
    SESSION_ID
  );

  await expect
    .poll(() => {
      const req = machineListRequests
        .filter((r) => r.params.get('demo_session') === SESSION_ID)
        .pop();
      return req?.params.get('offset');
    })
    .toBe('0');
  await expect(
    page.getByRole('checkbox', { name: 'Select Fan 1' })
  ).not.toBeChecked();
  await expect(page.getByText(/Selected on this page/)).toHaveCount(0);
  await expect(page.getByText('3 machines')).toBeVisible();
});

test('search shows location-total labeling and a truthful empty state', async ({
  page
}) => {
  await installMocks(page);
  await page.goto(
    '/playwright/machine-locations.html?mode=workspace&location=12'
  );
  await expect(page.getByText('Pump 1')).toBeVisible();

  await page.getByRole('textbox', { name: 'Search machines' }).fill('zzz');
  await expect(page.getByText('No machines match your search.')).toBeVisible();
  const clear = page.getByRole('button', { name: 'Clear search' });
  await expect(clear).toBeVisible();
  // Aggregate cards are location totals, not search matches.
  await expect(page.getByTestId('location-totals-note')).toBeVisible();
  await expect(page.getByTestId('machine-count-view')).toHaveText('9');

  await clear.click();
  await expect(page.getByText('Pump 1')).toBeVisible();
});

test('a failed machine read is not a zero count', async ({ page }) => {
  machinesError = true;
  await installMocks(page);
  await page.goto(
    '/playwright/machine-locations.html?mode=workspace&location=12'
  );
  // React Query retries the read three times before surfacing the error;
  // the eventual state must be a visible failure, never a reassuring zero.
  await expect(page.getByText('Machines unavailable')).toBeVisible({
    timeout: 20_000
  });
  await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible();
  await expect(page.getByText(/0 machines/)).toHaveCount(0);
});

test.describe('mobile', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('Choose location collapses the browser above the results', async ({
    page
  }) => {
    await installMocks(page);
    await page.goto(
      '/playwright/machine-locations.html?mode=workspace&location=12'
    );
    const choose = page.getByRole('button', { name: 'Choose location' });
    await expect(choose).toBeVisible();
    // The tree is collapsed by default on narrow screens; the path and the
    // result summary stay visible above the table.
    await expect(
      page.getByRole('tree', { name: 'Location tree' })
    ).toBeHidden();
    await expect(page.getByTestId('machine-count-view')).toBeVisible();

    await choose.click();
    await expect(
      page.getByRole('tree', { name: 'Location tree' })
    ).toBeVisible();

    // No document-wide horizontal overflow at phone widths.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test.describe('narrow phone', () => {
    test.use({ viewport: { width: 320, height: 720 } });

    test('Choose location does not overflow a 320px viewport', async ({
      page
    }) => {
      await installMocks(page);
      await page.goto(
        '/playwright/machine-locations.html?mode=workspace&location=12'
      );
      const choose = page.getByRole('button', { name: 'Choose location' });
      await expect(choose).toBeVisible();
      await choose.click();
      await expect(
        page.getByRole('tree', { name: 'Location tree' })
      ).toBeVisible();
      // Tree rows pack name/type/code/workspace on one nowrap line; opening
      // the browser must still fit the narrowest phone width.
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth
      );
      expect(overflow).toBeLessThanOrEqual(1);
    });
  });
});

test('machine return routes preserve the source view and filters', async ({
  page
}) => {
  await installMocks(page);
  await page.goto(
    `/playwright/machine-locations.html?mode=workspace&location=12&scope=direct&demo_session=${SESSION_ID}`
  );
  await page.getByRole('link', { name: 'Pump 1' }).click();
  // The panel router appends the default `details` panel; the source view and
  // cohort filters must survive that redirect.
  await expect(page.getByTestId('destination')).toContainText(
    `/machines/machine/5/details?from=sites&demo_session=${SESSION_ID}&location=12&scope=direct`
  );
  // Detail labels: the boolean is a record flag, not a run state.
  await expect(page.getByText('Active record')).toBeVisible();
  const breadcrumb = page.getByRole('link', { name: 'Machines' });
  await expect(breadcrumb).toHaveAttribute(
    'href',
    /\/machines\/index\/sites\/\?demo_session=123e4567-e89b-12d3-a456-426614174000&location=12&scope=direct$/
  );

  // All Machines origin returns to its own panel, never the remembered one.
  await page.goto(
    `/playwright/machine-locations.html?mode=machine&machine=6&from=machines&demo_session=${SESSION_ID}`
  );
  await expect(page.getByRole('link', { name: 'Machines' })).toHaveAttribute(
    'href',
    new RegExp(`/machines/index/machines/\\?demo_session=${SESSION_ID}$`)
  );
  await expect(
    page.getByText('No physical location assigned', { exact: true })
  ).toBeVisible();
});

test('collapsing an ancestor keeps the selection and stays collapsed', async ({
  page
}) => {
  await installMocks(page);
  await page.goto(
    '/playwright/machine-locations.html?mode=workspace&location=150'
  );
  await expect(page.locator('[role="treeitem"][data-value="150"]')).toBeVisible(
    { timeout: 15_000 }
  );
  await expect(
    page.getByRole('button', { name: 'Collapse Building B' })
  ).toBeVisible();

  await page.getByRole('button', { name: 'Collapse Building B' }).click();
  // The selection still targets Zenith Cell inside Building B; no background
  // reveal pass may reopen the branch the user just collapsed.
  await expect(
    page.getByRole('button', { name: 'Expand Building B' })
  ).toBeVisible();
  await page.waitForTimeout(1000);
  await expect(
    page.getByRole('button', { name: 'Collapse Building B' })
  ).toHaveCount(0);
  await expect(page.getByTestId('destination')).toContainText('location=150');
});

test('reveal stops at an exhausted branch instead of paging past it', async ({
  page
}) => {
  const { listRequests } = await installMocks(page);
  // Ghost Cell (pk 999) resolves as a detail record but is absent from every
  // Building B page; once the server reports no continuation the reveal must
  // stop and say so, not keep paging up to the reveal bound.
  await page.goto(
    '/playwright/machine-locations.html?mode=workspace&location=999'
  );
  await expect(
    page.getByText(
      'This location cannot be found in the tree. Use the search above to look for it.'
    )
  ).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText('beyond the loaded tree pages')).toHaveCount(0);
  const branchRequests = listRequests.filter(
    (r) => r.params.get('parent') === '11'
  );
  expect(branchRequests.length).toBeLessThanOrEqual(2);
});

test('a failed root read is not the create-first-site empty state', async ({
  page
}) => {
  locationsError = true;
  await installMocks(page);
  await page.goto('/playwright/machine-locations.html?mode=workspace');
  await expect(page.getByText('This branch could not be loaded.')).toBeVisible({
    timeout: 20_000
  });
  await expect(
    page.getByRole('button', { name: 'Retry' }).first()
  ).toBeVisible();
  await expect(
    page.getByText('Create your first site to organize machines.')
  ).toHaveCount(0);
});

test('machine breadcrumb without a source view keeps the legacy contract', async ({
  page
}) => {
  await installMocks(page);
  await page.goto(
    `/playwright/machine-locations.html?mode=machine&machine=5&demo_session=${SESSION_ID}&location=12&scope=direct`
  );
  await expect(page.getByRole('link', { name: 'Machines' })).toHaveAttribute(
    'href',
    new RegExp(
      `/machines/index/\\?demo_session=${SESSION_ID}&location=12&scope=direct$`
    )
  );
});
