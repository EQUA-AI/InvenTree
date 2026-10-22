import { expect, test } from '@playwright/test';

import { nativeBaseline } from '../s0/guards';
import { mockExplorer, openExplorer } from '../s1/machineExplorerFixtures';
import {
  mockFaultHistory,
  mockMachineWork,
  occurrenceDetail,
  occurrences,
  openFaultHistory,
  openMaintenance
} from './machineWorkFixtures';
import { informationTooltip } from './presentationAssertions';

nativeBaseline();

/**
 * The panel never runs past the viewport; wide tables scroll inside
 * themselves. Measured on the panel, not the document: the fixture's own
 * destination line is harness chrome and may be wider than a phone.
 */
async function expectContained(page: import('@playwright/test').Page) {
  const width = page.viewportSize()!.width;
  for (const id of ['machine-maintenance', 'fault-occurrences']) {
    const panel = page.getByTestId(id);
    if (!(await panel.count())) continue;
    const box = await panel.evaluate((el) => {
      const rect = el.getBoundingClientRect();
      return {
        right: rect.right,
        scroll: el.scrollWidth,
        client: el.clientWidth
      };
    });
    expect(box.right, `${id} right edge`).toBeLessThanOrEqual(width);
    expect(box.scroll, `${id} overflow`).toBeLessThanOrEqual(box.client + 1);
  }
}

async function expectOccurrenceCount(
  page: import('@playwright/test').Page,
  count: number
) {
  const panel = page.getByTestId('fault-occurrences');
  await expect(
    panel.getByRole('heading', { name: 'Occurrences', level: 4, exact: true })
  ).toBeVisible();
  const badge = panel.getByRole('button', { name: String(count), exact: true });
  await expect(badge).toBeVisible();
  await page.mouse.move(0, 0);
  await page.keyboard.press('Tab');
  await badge.focus();
  await informationTooltip(
    page,
    badge,
    `${count} ${count === 1 ? 'occurrence' : 'occurrences'} · Newest first`
  );
}

test.describe('R6 P12 machine Maintenance: Open work and Completed history', () => {
  test('open work and completed history are separate views with agreeing counts', async ({
    page
  }, testInfo) => {
    const requests = await mockMachineWork(page, { jobs: 20 });
    for (const scheme of ['light', 'dark'] as const) {
      await openMaintenance(page, { scheme });
      const views = page.getByRole('radiogroup', { name: 'Maintenance view' });
      await expect(views.getByText('Open work')).toBeVisible();
      await expect(views).toContainText('20');
      await expect(views).toContainText('Completed history');
      await expect(views).toContainText('2');
      // Both kinds of work exist; neither is read as the machine's verdict.
      await expect(
        page.getByText('a completed job does not close the open work')
      ).toHaveCount(0);
      const table = page.getByTestId('machine-open-work');
      await expect(table.locator('[data-work]')).toHaveCount(20);
      await expect(page.getByText('20 open work orders')).toBeVisible();
      const phone = testInfo.project.name.includes('phone');
      // Desktop columns; a phone shows the same fields as one card per job.
      for (const header of phone
        ? ['Component', 'Owner', 'Window']
        : ['Priority', 'Work', 'Component', 'Lifecycle', 'Owner', 'Window'])
        await expect(
          phone
            ? table
                .locator('[data-work="73"]')
                .getByText(header, { exact: true })
            : table.getByRole('columnheader', { name: header, exact: true })
        ).toBeVisible();
      const first = table.locator('[data-work="73"]');
      await expect(first.getByRole('link', { name: 'WO-73' })).toHaveAttribute(
        'href',
        '/maintenance/work-orders/73/'
      );
      await expect(first).toContainText('Drive-end bearing');
      await expect(first).toContainText('Synthetic Technician');
      await expect(first).toContainText('Planned');
      await expect(first).toContainText('18 Oct 2026');
      await expect(table.locator('[data-work="74"]')).toContainText(
        'Not recorded'
      );
      await expect(table.locator('[data-work="74"]')).toContainText(
        'Not scheduled'
      );
      await expect(table.locator('[data-work="75"]')).toContainText(
        'Legacy assignee: Legacy crew'
      );
      // Opening work is the only row action: no completion from this table.
      await expect(table.getByRole('button')).toHaveCount(0);
      await expectContained(page);
      await page.screenshot({
        path: testInfo.outputPath(`maintenance-open-${scheme}.png`),
        fullPage: true,
        animations: 'disabled'
      });
    }
    // The open list is the native list, narrowed to this machine's open set.
    const read = requests.list.at(-1)!.searchParams;
    expect(read.get('machine')).toBe('18');
    expect(read.get('lifecycle_group')).toBe('open');
    expect(read.get('limit')).toBe('25');
    expect(requests.records).toHaveLength(0);
  });

  test('50 open jobs page through, and Back from a work order restores the view', async ({
    page
  }) => {
    const requests = await mockMachineWork(page, { jobs: 50 });
    await openMaintenance(page, { query: '?from=machines&search=pump' });
    await expect(page.getByText('50 open work orders')).toBeVisible();
    await page
      .getByRole('navigation', { name: 'Open work pagination' })
      .getByRole('button', { name: '2' })
      .click();
    await expect(
      page.getByTestId('machine-open-work').locator('[data-work]')
    ).toHaveCount(25);
    await expect(page.getByTestId('destination')).toContainText('work_page=2');
    expect(requests.list.at(-1)!.searchParams.get('offset')).toBe('25');
    // The Machines list's own return state is kept beside the tab's state.
    await expect(page.getByTestId('destination')).toContainText(
      'from=machines'
    );
    await page.getByRole('link', { name: 'WO-98', exact: true }).click();
    await expect(page.getByTestId('destination')).toHaveText(
      '/maintenance/work-orders/98/'
    );
    await expect(
      page.getByRole('heading', { name: 'WO-98: Synthetic job 98' })
    ).toBeVisible();
    await page.getByTestId('work-order-back').click();
    await expect(page.getByTestId('destination')).toContainText(
      '/machines/machine/18/maintenance/'
    );
    await expect(page.getByTestId('destination')).toContainText('work_page=2');
    await expect(
      page.getByTestId('machine-open-work').locator('[data-work="98"]')
    ).toBeVisible();
  });

  test('search narrows open work and says when nothing matches', async ({
    page
  }) => {
    const requests = await mockMachineWork(page, { jobs: 20 });
    await openMaintenance(page);
    const search = page.getByRole('textbox', { name: 'Search open work' });
    await search.fill('WO-81');
    await expect(page.getByText('1 open work order')).toBeVisible();
    expect(requests.list.at(-1)!.searchParams.get('search')).toBe('WO-81');
    await search.fill('no such job');
    await expect(
      page.getByText('No open work matches this search')
    ).toBeVisible();
    await page.getByRole('button', { name: 'Clear search' }).click();
    await expect(page.getByText('20 open work orders')).toBeVisible();
    // A narrowed list is not reported as a disagreement with the count.
    await expect(page.getByText('Counts changed while loading')).toHaveCount(0);
  });

  test('completed history is the native table, not filtered by the page URL', async ({
    page
  }, testInfo) => {
    const requests = await mockMachineWork(page, { jobs: 3 });
    for (const scheme of ['light', 'dark'] as const) {
      await openMaintenance(page, {
        scheme,
        query: '?from=machines&search=pump&work_view=completed'
      });
      await expect(page.getByText('Drive-end bearing replaced')).toBeVisible();
      await expect(page.getByText('Legacy service visit')).toBeVisible();
      await expect(
        page.getByRole('link', { name: 'Open work order WO-60' })
      ).toBeVisible();
      await expect(page.getByText('Follow-up required')).toBeVisible();
      const component = page
        .getByRole('row')
        .filter({ hasText: 'Legacy service visit' });
      for (const name of [
        '— Work order link not available',
        '— Component not recorded'
      ]) {
        const missing = component.getByRole('cell', { name, exact: true });
        await expect(missing).toBeVisible();
        await expect(missing.getByRole('button')).toHaveCount(0);
      }
      await expect(
        component.getByRole('cell', { name: 'No', exact: true })
      ).toBeVisible();
      const information = component.getByRole('button', {
        name: 'Record information',
        exact: true
      });
      await information.focus();
      const missingInformation = await informationTooltip(
        page,
        information,
        'Work order link not available'
      );
      await expect(missingInformation).toContainText('Component not recorded');
      await expectContained(page);
      await page.screenshot({
        path: testInfo.outputPath(`maintenance-completed-${scheme}.png`),
        fullPage: true,
        animations: 'disabled'
      });
    }
    // The Machines list's search is not this table's search.
    for (const url of requests.records) {
      expect(url.searchParams.get('machine')).toBe('18');
      expect(url.searchParams.get('search') ?? '').toBe('');
      expect(url.searchParams.has('from')).toBe(false);
      expect(url.searchParams.has('work_view')).toBe(false);
    }
    expect(requests.records.length).toBeGreaterThan(0);
  });

  test('a defensive unavailable projection is never zero', async ({ page }) => {
    const requests = await mockMachineWork(page, { open: null });
    await openMaintenance(page);
    const views = page.getByRole('radiogroup', { name: 'Maintenance view' });
    await expect(views).toContainText('not available');
    await expect(
      page.getByText('Open work could not be checked')
    ).toBeVisible();
    await expect(
      page.getByText('This is not the same as having no open work.')
    ).toBeVisible();
    await expect(page.getByText('No open work', { exact: true })).toHaveCount(
      0
    );
    expect(requests.list).toHaveLength(0);
  });

  test('failed counts and a failed list stay distinct from empty', async ({
    page
  }) => {
    await mockMachineWork(page, { summaryStatus: 503 });
    await openMaintenance(page);
    await expect(
      page.getByText('Maintenance counts could not be loaded')
    ).toBeVisible();
    await expect(
      page.getByRole('radiogroup', { name: 'Maintenance view' })
    ).toContainText('not available');
    await expect(page.getByText('No open work', { exact: true })).toHaveCount(
      0
    );
    await expect(
      page.getByText('Open work could not be checked')
    ).toBeVisible();
    await expect(
      page.getByTestId('machine-maintenance').locator('.mantine-Skeleton-root')
    ).toHaveCount(0);
    await expect(
      page.getByRole('button', { name: 'Retry counts' })
    ).toBeVisible();
  });

  test('a denied summary has an explicit open-work state without a retryable failure', async ({
    page
  }, info) => {
    const requests = await mockMachineWork(page, { summaryStatus: 404 });
    await openMaintenance(page);
    await expect(
      page.getByText(
        'Open work is not available for this machine in your scope.'
      )
    ).toBeVisible();
    await expect(
      page.getByTestId('machine-maintenance').locator('.mantine-Skeleton-root')
    ).toHaveCount(0);
    await expect(
      page.getByRole('button', { name: 'Retry counts' })
    ).toHaveCount(0);
    expect(requests.list).toHaveLength(0);
    await page.screenshot({
      path: info.outputPath('maintenance-denied.png'),
      fullPage: true
    });
  });

  test('a failed open-work read shows no cached rows', async ({ page }) => {
    await mockMachineWork(page, { listStatus: 503 });
    await openMaintenance(page);
    await expect(page.getByText('Open work could not be loaded')).toBeVisible();
    await expect(page.getByTestId('machine-open-work')).toHaveCount(0);
    await expect(
      page.getByRole('button', { name: 'Retry open work' })
    ).toBeVisible();
  });

  test('a machine with no open work says so and offers completed history', async ({
    page
  }) => {
    await mockMachineWork(page, { jobs: 0, open: 0 });
    await openMaintenance(page);
    await expect(page.getByText('No open work', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Show completed history' }).click();
    await expect(page.getByText('Drive-end bearing replaced')).toBeVisible();
    await expect(page.getByTestId('destination')).toContainText(
      'work_view=completed'
    );
  });

  test('an unknown view in the link is refused with a way back', async ({
    page
  }) => {
    const requests = await mockMachineWork(page);
    await openMaintenance(page, { query: '?work_view=all' });
    await expect(page.getByText('Maintenance view unavailable')).toBeVisible();
    await page.getByRole('button', { name: 'Show open work' }).click();
    await expect(page.getByTestId('machine-open-work')).toBeVisible();
    expect(requests.list.length).toBeGreaterThan(0);
  });
});

test('the Machines list Work count opens that machine on its open work', async ({
  page
}) => {
  await mockExplorer(page);
  await page.route('**/api/assets/machines/1/work-summary/', (route) =>
    route.fulfill({
      json: {
        rule: 'machine_work_views.v1',
        machine: 1,
        open_statuses: ['planned'],
        open: { count: 2, highest_priority: 'high' },
        completed: { count: 0, source: 'maintenance records' }
      }
    })
  );
  const listed: URL[] = [];
  await page.route('**/api/kanban/work-orders/list/**', (route) => {
    listed.push(new URL(route.request().url()));
    return route.fulfill({ json: { count: 2, results: [], next: null } });
  });
  await openExplorer(page, '/machines/index/machines/?search=pump');
  const cell = page
    .locator('[data-machine]')
    .filter({ hasText: 'Synthetic pump 001' })
    .locator('td[data-open-work="2"]');
  const link = cell.getByRole('link', { name: /^2 open/ });
  const href = (await link.getAttribute('href')) ?? '';
  expect(href.split('?')[0]).toBe('/machines/machine/1/maintenance/');
  const carried = new URLSearchParams(href.split('?')[1]);
  expect(carried.get('from')).toBe('machines');
  expect(carried.get('search')).toBe('pump');
  await link.click();
  await expect(page.getByTestId('machine-maintenance')).toBeVisible();
  await expect(
    page.getByRole('radiogroup', { name: 'Maintenance view' })
  ).toContainText('2');
  expect(listed.at(-1)!.searchParams.get('machine')).toBe('1');
  await expect(page.getByTestId('destination')).toContainText('from=machines');
});

test.describe('R6 P12 Fault History: occurrences, retained evidence and owning work', () => {
  test('occurrences keep their own ids, retained evidence and owning work', async ({
    page
  }, testInfo) => {
    const requests = await mockFaultHistory(page);
    for (const scheme of ['light', 'dark'] as const) {
      await openFaultHistory(page, { scheme });
      // The native rollup is kept above the occurrences.
      await expect(page.getByTestId('fault-history-panel')).toBeVisible();
      await expect(page.getByText('Verified causes')).toBeVisible();
      await expectOccurrenceCount(page, 3);
      const bearing = page.locator('[data-occurrence="901"]').first();
      await expect(bearing).toContainText('Drive-end bearing high temperature');
      const information = bearing.getByRole('button', {
        name: 'Event information',
        exact: true
      });
      await page.keyboard.press('Tab');
      await information.focus();
      const eventInformation = await informationTooltip(
        page,
        information,
        'Code TT-HI'
      );
      await expect(eventInformation).toContainText('Source alarm ALM-77812');
      await expect(eventInformation).toContainText('Record 901');
      await expect(bearing).toContainText('Drive-end bearing');
      await expect(bearing).toContainText('Unverified component assignment');
      await expect(bearing).toContainText('1 retained snapshot');
      await expect(bearing.getByRole('link', { name: 'WO-86' })).toBeVisible();
      await expect(bearing).toContainText('Resolved');
      // No source id is invented where the source sent none.
      await information.blur();
      await page.mouse.move(0, 0);
      await expect(page.locator('[role="tooltip"]:visible')).toHaveCount(0);
      const pressure = page.locator('[data-occurrence="902"]').first();
      await page.keyboard.press('Tab');
      const pressureInformation = pressure.getByRole('button', {
        name: 'Event information',
        exact: true
      });
      await pressureInformation.focus();
      const pressureTooltip = await informationTooltip(
        page,
        pressureInformation,
        'Record 902'
      );
      await expect(pressureTooltip).not.toContainText('Source alarm');
      await expect(pressure).toContainText('Signal not mapped to a component');
      await expect(pressure).toContainText('No retained snapshot');
      await expect(
        page.locator('[data-occurrence="900"]').first().getByRole('button', {
          name: 'No linked work order is available to you',
          exact: true
        })
      ).toHaveText('—');
      await expectContained(page);
      await page.screenshot({
        path: testInfo.outputPath(`fault-history-${scheme}.png`),
        fullPage: true,
        animations: 'disabled'
      });
    }
    expect(requests.list.at(-1)!.searchParams.get('status')).toBe('all');
  });

  test('opening an occurrence shows what was retained then, not current readings', async ({
    page
  }, testInfo) => {
    const requests = await mockFaultHistory(page);
    for (const scheme of ['light', 'dark'] as const) {
      await openFaultHistory(page, { scheme });
      const trigger = page
        .locator('[data-occurrence="901"]')
        .first()
        .getByRole('button', { name: 'Evidence' });
      await trigger.click();
      const evidence = page.getByTestId('occurrence-evidence');
      await expect(evidence).toBeVisible();
      await expect(evidence.getByText('Original observation')).toBeVisible();
      await expect(
        evidence.getByText(
          'Bearing temperature 118.4 degC above its 110 degC limit.'
        )
      ).toBeVisible();
      await expect(
        evidence.getByText(/Acknowledged .* by Synthetic Supervisor/)
      ).toBeVisible();
      const snapshot = evidence.locator('[data-snapshot]');
      await expect(snapshot).toContainText('Retained snapshot');
      await expect(snapshot).toContainText('118.4 degC');
      await expect(snapshot).toContainText(
        'Captured for the repair raised for it'
      );
      await expect(snapshot).toContainText('content hash c0ffeec0ffee');
      // Retained source times stay on the plant clock, even for migrated history.
      await expect(snapshot).toContainText('12 Oct 2026');
      await expect(snapshot).toContainText('Captured by Synthetic Technician');
      await expect(snapshot).toContainText('System actor: historian-import');
      await expect(
        snapshot.getByRole('link', { name: 'Open exact retained snapshot' })
      ).toHaveAttribute(
        'href',
        '/machines/machine/18/health/?snapshot=a3f1c2d4-0000-4000-8000-000000000001'
      );
      await expect(page.getByTestId('fault-occurrences')).not.toContainText(
        'Times: your clock'
      );
      const displayedObservation = await page.evaluate(
        (instant) =>
          new Intl.DateTimeFormat('en-GB', {
            dateStyle: 'medium',
            timeStyle: 'medium',
            hour12: false
          })
            .format(new Date(instant))
            .replace(',', ''),
        '2026-10-12T02:14:05Z'
      );
      await expect(
        snapshot.getByRole('cell', {
          name: displayedObservation,
          exact: true
        })
      ).toBeVisible();
      await expect(
        evidence.getByRole('link', {
          name: 'Current readings are on the Health tab'
        })
      ).toHaveAttribute('href', '/machines/machine/18/health/');
      await expect(evidence.getByText('Repair packet RP-901')).toBeVisible();
      await expect(page.getByTestId('destination')).toContainText(
        'occurrence=901'
      );
      await expectContained(page);
      await page.screenshot({
        path: testInfo.outputPath(`fault-evidence-${scheme}.png`),
        fullPage: true,
        animations: 'disabled'
      });
      // Closing returns focus to the control that opened it.
      await page
        .locator('[data-occurrence="901"]')
        .first()
        .getByRole('button', { name: 'Hide evidence' })
        .click();
      await expect(evidence).toHaveCount(0);
      await expect(trigger).toBeFocused();
    }
    expect(requests.detail.map((url) => url.pathname)).toContain(
      '/api/assets/machines/18/fault-history/occurrences/901/'
    );
  });

  test('an occurrence link opens that occurrence, and no evidence is said plainly', async ({
    page
  }) => {
    await mockFaultHistory(page);
    await openFaultHistory(page, { query: '?occurrence=902' });
    const evidence = page.getByTestId('occurrence-evidence');
    await expect(
      evidence.getByText(
        "No evidence snapshot was retained for this occurrence. The record above is what was kept; today's readings are not put in its place."
      )
    ).toBeVisible();
    await expect(evidence.getByRole('link', { name: 'WO-73' })).toBeVisible();
  });

  test('active and resolved filters, empty and failed reads stay distinct', async ({
    page
  }) => {
    const requests = await mockFaultHistory(page);
    await openFaultHistory(page);
    await page
      .getByRole('radiogroup', { name: 'Occurrence status' })
      .getByText('Resolved')
      .click();
    await expectOccurrenceCount(page, 1);
    await expect(
      page.getByRole('radio', { name: 'Resolved', exact: true })
    ).toBeChecked();
    expect(requests.list.at(-1)!.searchParams.get('status')).toBe('resolved');
    await page
      .getByRole('radiogroup', { name: 'Occurrence status' })
      .getByText('Active')
      .click();
    await expectOccurrenceCount(page, 2);
    await expect(
      page.getByRole('radio', { name: 'Active', exact: true })
    ).toBeChecked();
    expect(requests.list.at(-1)!.searchParams.get('status')).toBe('active');
  });

  test('no recorded occurrence is a successful empty state', async ({
    page
  }) => {
    await mockFaultHistory(page, { empty: true });
    await openFaultHistory(page);
    await expect(
      page.getByText(
        'No alarm or detected condition has been recorded for this machine.'
      )
    ).toBeVisible();
    await expect(page.getByText('Verified causes')).toBeVisible();
  });

  test('a failed occurrence read keeps the rollup and shows nothing in its place', async ({
    page
  }) => {
    await mockFaultHistory(page, { listStatus: 503 });
    await openFaultHistory(page);
    await expect(
      page.getByText('Occurrences could not be loaded')
    ).toBeVisible();
    await expect(page.getByText('Verified causes')).toBeVisible();
    await expect(page.locator('[data-occurrence]')).toHaveCount(0);
  });
});

test('a filtered-out occurrence link still reads its independent evidence', async ({
  page
}, info) => {
  const requests = await mockFaultHistory(page);
  await openFaultHistory(page, {
    query: '?fault_status=active&occurrence=901'
  });
  await expect(page.locator('[data-occurrence="901"]')).toHaveCount(0);
  await expect(page.getByTestId('occurrence-evidence')).toContainText(
    '118.4 degC'
  );
  expect(requests.detail.map((url) => url.pathname)).toContain(
    '/api/assets/machines/18/fault-history/occurrences/901/'
  );
  await expectContained(page);
  await page.screenshot({
    path: info.outputPath('linked-evidence-outside-list.png'),
    fullPage: true
  });
});

test('a missing linked occurrence states unavailable independently of the list', async ({
  page
}) => {
  await mockFaultHistory(page);
  await openFaultHistory(page, { query: '?occurrence=640' });
  await expect(
    page.getByText('This occurrence is not available to you.')
  ).toBeVisible();
  await expectOccurrenceCount(page, 3);
});

test('out-of-range occurrence pages recover without claiming an empty history', async ({
  page
}) => {
  const requests = await mockFaultHistory(page);
  await openFaultHistory(page, {
    query: '?fault_status=active&fault_page=2&from=machines'
  });
  await expect(page.locator('[data-occurrence]')).toHaveCount(2);
  await expect(page.getByTestId('destination')).not.toContainText(
    'fault_page=2'
  );
  await expect(page.getByTestId('destination')).toContainText('from=machines');
  await expect(
    page.getByText('No occurrence is active on this machine now.')
  ).toHaveCount(0);
  expect(requests.list.map((url) => url.searchParams.get('offset'))).toContain(
    '25'
  );
  expect(requests.list.at(-1)!.searchParams.get('offset')).toBe('0');
});

test('acknowledgement without a recorder retains its time and state', async ({
  page
}) => {
  await mockFaultHistory(page);
  await page.route('**/occurrences/900/', (route) =>
    route.fulfill({
      json: {
        ...occurrenceDetail(900),
        acknowledged_by_name: null
      }
    })
  );
  await openFaultHistory(page, { query: '?occurrence=900' });
  const evidence = page.getByTestId('occurrence-evidence');
  await expect(evidence).toContainText('Acknowledged 10 Oct 2026 11:05:00');
  await expect(evidence).toContainText('recorder not recorded');
  await expect(
    evidence.getByText('Not acknowledged', { exact: true })
  ).toHaveCount(0);
});

test('an occurrence older than the first page opens without changing the current list', async ({
  page
}) => {
  await mockFaultHistory(page);
  const newer = Array.from({ length: 26 }, (_, index) => ({
    ...occurrences[0],
    id: 1000 - index
  }));
  await page.route('**/fault-history/occurrences/?*', (route) =>
    route.fulfill({
      json: {
        rule: 'fault_occurrences.v1',
        machine: 18,
        count: 26,
        limit: 25,
        offset: 0,
        results: newer.slice(0, 25)
      }
    })
  );
  await openFaultHistory(page, { query: '?occurrence=901' });
  await expect(page.locator('[data-occurrence]')).toHaveCount(25);
  await expect(page.locator('[data-occurrence="901"]')).toHaveCount(0);
  await expect(page.getByTestId('occurrence-evidence')).toContainText(
    '118.4 degC'
  );
  await expect(
    page.getByRole('region', { name: 'Opened occurrence evidence' })
  ).toBeFocused();
});

test('a failed refresh removes stale counts and open-work rows', async ({
  page
}) => {
  await mockMachineWork(page, { jobs: 20 });
  await openMaintenance(page);
  await expect(page.getByText('20 open work orders')).toBeVisible();
  await page.route('**/api/assets/machines/18/work-summary/', (route) =>
    route.fulfill({
      status: 503,
      json: { detail: 'Synthetic unavailable source' }
    })
  );
  await page
    .getByTestId('machine-maintenance')
    .getByRole('button', { name: 'Refresh', exact: true })
    .click();
  await expect(page.getByText('Open work could not be checked')).toBeVisible();
  const views = page.getByRole('radiogroup', { name: 'Maintenance view' });
  await expect(views).toContainText('not available');
  await expect(views).not.toContainText('20');
  await expect(page.getByTestId('machine-open-work')).toHaveCount(0);
  await expect(
    page.getByTestId('machine-maintenance').locator('.mantine-Skeleton-root')
  ).toHaveCount(0);
});
