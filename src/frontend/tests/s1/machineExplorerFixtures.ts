import { type Page, expect } from '@playwright/test';

export async function mockExplorer(page: Page) {
  const location = {
    pk: 12,
    client: 1,
    parent: null,
    name: 'Synthetic maintenance site',
    code: 'S1-SITE',
    kind: 'site',
    description: 'Synthetic location — not a plant deployment',
    timezone: 'UTC',
    effective_timezone: 'UTC',
    archived: false,
    version: 1,
    path: [{ pk: 12, name: 'Synthetic maintenance site' }],
    has_children: true,
    counts: {
      direct_machines: 60,
      total_machines: 80,
      direct_open_work_orders: 2,
      total_open_work_orders: 3
    }
  };
  const child = {
    ...location,
    pk: 21,
    parent: 12,
    name: 'Synthetic pump hall',
    code: 'S1-HALL',
    kind: 'area',
    path: [...location.path, { pk: 21, name: 'Synthetic pump hall' }],
    has_children: false
  };
  const archivedLocation = {
    ...location,
    pk: 90,
    name: 'Archived synthetic site',
    archived: true,
    path: [{ pk: 90, name: 'Archived synthetic site' }]
  };
  const archivedChild = {
    ...child,
    pk: 91,
    parent: 90,
    name: 'Archived-site room',
    path: [...archivedLocation.path, { pk: 91, name: 'Archived-site room' }]
  };
  const machines = Array.from({ length: 110 }, (_, index) => ({
    pk: index + 1,
    name: `Synthetic pump ${String(index + 1).padStart(3, '0')}`,
    active: index !== 9,
    client: 1,
    asset_type: index === 0 ? 'pumphouse' : index === 3 ? 'pump' : 'machine',
    parent: index === 3 ? 1 : null,
    source_key: '',
    location: '',
    manufacturer: 'Synthetic manufacturer',
    model: 'Synthetic model',
    serial: `S1-${index + 1}`,
    description: 'Synthetic identity retained in native Details',
    physical_location: index < 60 ? location : index < 80 ? child : null,
    placement_version: 1,
    barcode_hash: '',
    open_alarms: index < 50 || index >= 80 ? 1 : 0,
    open_critical_alarms: 0,
    // Row 001 includes child alarms; row 004 has info only. Own warnings
    // appear only on assessed Warning/Critical or unavailable rows.
    open_warning_alarms:
      (index < 50 || index >= 80) && (index % 5 === 1 || index % 5 === 2)
        ? 1
        : 0,
    // R6 UI01: synthetic open Work — busy, quiet and one unresolved row.
    open_work:
      index === 2
        ? null
        : index % 3 === 0
          ? { count: 2, highest_priority: 'high' }
          : index % 3 === 1
            ? { count: 1, highest_priority: 'low' }
            : { count: 0, highest_priority: null },
    // R6 UI01: Health condition and coverage; pump 003 cannot be judged.
    condition:
      index === 2
        ? null
        : [
            {
              state: 'normal',
              configured: true,
              signal_count: 6,
              stale_signal_count: 0,
              degraded_data: false
            },
            {
              state: 'warning',
              configured: true,
              signal_count: 6,
              stale_signal_count: 2,
              degraded_data: true
            },
            {
              state: 'critical',
              configured: true,
              signal_count: 6,
              stale_signal_count: 0,
              degraded_data: true
            },
            {
              state: 'unknown',
              configured: false,
              signal_count: 0,
              stale_signal_count: 0,
              degraded_data: false
            },
            {
              state: 'offline',
              configured: true,
              signal_count: 6,
              stale_signal_count: 6,
              degraded_data: true
            }
          ][index % 5],
    // R6 UI01: declared criticality, including machines that declare none.
    criticality: (['critical', 'high', null, 'medium', 'low'] as const)[
      index % 5
    ]
  }));
  const requests: { method: string; params: URLSearchParams }[] = [];
  const locationRequests: URLSearchParams[] = [];
  const state = {
    status: 200,
    canChange: false,
    locationStatus: 200,
    totalLimit: 110,
    withArchivedLocation: false,
    // No machine matches any criticality filter.
    noCriticalityMatch: false
  };
  await page.route('**/api/notifications/?*', (route) =>
    route.fulfill({ json: { count: 0, results: [], next: null } })
  );
  await page.route(/\/api\/ai\/threads(?:\?|$)/, (route) =>
    route.fulfill({ json: { threads: [], shared_threads: [] } })
  );
  await page.route('**/api/ai/voice/capability', (route) =>
    route.fulfill({ json: { enabled: false, voices: [] } })
  );
  await page.route('**/api/aichat/ui/capabilities/', (route) =>
    route.fulfill({
      json: {
        version: 1,
        risk_radar: false,
        command_center: false,
        maintenance_metrics: 1
      }
    })
  );
  await page.route('**/api/assets/locations/context/', (route) =>
    route.fulfill({
      json: {
        workspaces: [{ pk: 1, name: 'Synthetic workspace' }],
        plant_timezone: null,
        can_add: false,
        can_change: state.canChange
      }
    })
  );
  const countAt = (node: { pk: number }, activeOnly: boolean) => {
    const rows = machines
      .slice(0, state.totalLimit)
      .filter(
        (machine) =>
          (!activeOnly || machine.active) &&
          machine.physical_location?.path.some((part) => part.pk === node.pk)
      );
    return {
      direct_machines: rows.filter(
        (machine) => machine.physical_location?.pk === node.pk
      ).length,
      total_machines: rows.length
    };
  };
  await page.route('**/api/assets/locations/?*', (route) => {
    const params = new URL(route.request().url()).searchParams;
    locationRequests.push(params);
    const parent = params.get('parent');
    const nodes = [location, child];
    if (state.withArchivedLocation) nodes.push(archivedLocation, archivedChild);
    const rows = nodes.filter(
      (node) =>
        (parent == null ||
          (parent === 'root'
            ? node.parent == null
            : node.parent === Number(parent))) &&
        (params.get('active_only') !== 'true' ||
          !node.path.some((part) => part.pk === archivedLocation.pk))
    );
    return route.fulfill({
      json: {
        count: rows.length,
        results: rows.map((node) => ({
          ...node,
          counts:
            params.get('include_counts') === 'true'
              ? countAt(node, params.get('active') === 'true')
              : node.counts
        })),
        next: null
      }
    });
  });
  await page.route(/\/api\/assets\/locations\/\d+\/(?:\?.*)?$/, (route) => {
    const url = new URL(route.request().url());
    const node = url.pathname.includes('/21/') ? child : location;
    const rows = machines
      .slice(0, state.totalLimit)
      .filter(
        (machine) =>
          (!url.searchParams.has('active') ||
            machine.active === (url.searchParams.get('active') === 'true')) &&
          machine.physical_location?.path.some((part) => part.pk === node.pk)
      );
    return route.fulfill({
      status: state.locationStatus,
      json:
        state.locationStatus === 200
          ? {
              ...node,
              counts: {
                ...node.counts,
                direct_machines: rows.filter(
                  (machine) => machine.physical_location?.pk === node.pk
                ).length,
                total_machines: rows.length
              }
            }
          : { detail: 'Not found' }
    });
  });
  await page.route('**/api/assets/locations/machines/**', (route) => {
    const params = new URL(route.request().url()).searchParams;
    requests.push({ method: route.request().method(), params });
    if (state.status !== 200) {
      return route.fulfill({
        status: state.status,
        json: { detail: 'Synthetic unavailable source' }
      });
    }
    if (params.has('machine')) {
      return route.fulfill({
        json: {
          count: 1,
          results: machines.filter(
            (machine) => String(machine.pk) === params.get('machine')
          ),
          next: null
        }
      });
    }
    const search = (params.get('search') ?? '').trim().toLowerCase();
    const rows = machines.slice(0, state.totalLimit).filter((machine) => {
      if (
        params.has('active') &&
        machine.active !== (params.get('active') === 'true')
      )
        return false;
      const placement = machine.physical_location?.pk;
      if (
        params.get('active_only') === 'true' &&
        machine.physical_location?.path.some(
          (part) => part.pk === archivedLocation.pk
        )
      )
        return false;
      if (params.get('unassigned') === 'true' && placement != null)
        return false;
      if (
        params.has('location') &&
        (params.get('include_descendants') === 'false'
          ? placement !== Number(params.get('location'))
          : !machine.physical_location?.path.some(
              (part) => part.pk === Number(params.get('location'))
            ))
      )
        return false;
      if (params.get('has_alarms') === 'true' && !machine.open_alarms)
        return false;
      if (
        params.has('criticality') &&
        (state.noCriticalityMatch ||
          machine.criticality !== params.get('criticality'))
      )
        return false;
      return [machine.name, machine.serial, machine.manufacturer].some(
        (value) => value.toLowerCase().includes(search)
      );
    });
    const offset = Number(params.get('offset') ?? 0);
    const limit = Number(params.get('limit') ?? 25);
    return route.fulfill({
      json: {
        count: rows.length,
        results: rows.slice(offset, offset + limit),
        next: offset + limit < rows.length ? 'next' : null
      }
    });
  });
  await page.route(/\/api\/assets\/machines\/\d+\/$/, (route) => {
    const machinePk = Number(
      new URL(route.request().url()).pathname.split('/').at(-2)
    );
    return route.fulfill({
      json: machines.find((machine) => machine.pk === machinePk)
    });
  });
  return {
    requests,
    locationRequests,
    state,
    location,
    child,
    machines,
    archivedLocation,
    archivedChild
  };
}

export async function openExplorer(
  page: Page,
  entry: string,
  scheme = 'light'
) {
  const query = new URLSearchParams({
    surface: 'machine-explorer',
    sprint: 's1',
    role: 'viewer',
    scheme,
    entry
  });
  await page.goto(`/playwright/s0-native.html?${query}`);
  await showExplorerControls(page);
  await expect(
    page.getByRole('textbox', { name: 'Search machines', exact: true })
  ).toBeVisible();
}

/** Reveal the same native controls an operator uses on phone. */
export async function showExplorerControls(page: Page) {
  for (const name of ['Filters', 'Location details']) {
    const control = page.getByRole('button', { name, exact: true });
    if (
      (await control.isVisible()) &&
      (await control.getAttribute('aria-expanded')) === 'false'
    )
      await control.click();
  }
}

export function machineReturnLink(page: Page) {
  return page.getByRole('link', { name: 'breadcrumb-0-machines', exact: true });
}

export async function explorerParams(page: Page) {
  const destination = await page.getByTestId('destination').innerText();
  return new URL(destination, 'https://example.invalid').searchParams;
}
