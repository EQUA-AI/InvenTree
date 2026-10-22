import { type Page, expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { mockMachine } from '../s0/machineFixtures';
import { mockWorkList } from '../s1/workListFixtures';
import { anomaly, openHealth } from '../s3/healthFixtures';
import { BAYS, stationMimic } from '../s3/stationFixtures';
import { continuityWork } from '../s4/continuityFixtures';
import { revealWorkContext } from '../s4/workExecutionFixtures';
import {
  capturePanel,
  expectNoPageOverflow,
  mockExecutionWork,
  openExecutionPanel
} from '../s4/workExecutionFixtures';
import { closeoutWork, story } from '../s6/closeoutFixtures';

nativeBaseline(undefined, { routeLocalSources: false });

test.beforeEach(async ({ page }) => {
  await page.route(
    /^https:\/\/cdn\.svar\.dev\/fonts\/roboto\/regular\.woff2?$/,
    (route) => route.fulfill({ status: 503, body: '' })
  );
});

function watchWrites(page: Page) {
  const writes: string[] = [];
  page.on('request', (request) => {
    if (
      new URL(request.url()).pathname.startsWith('/api/') &&
      !['GET', 'OPTIONS'].includes(request.method())
    )
      writes.push(`${request.method()} ${request.url()}`);
  });
  return writes;
}

async function noWriteControls(page: Page, names: RegExp, minimum = 0) {
  const controls = page.getByRole('button', { name: names });
  expect(
    await controls.count(),
    'Expected permission controls to be present'
  ).toBeGreaterThanOrEqual(minimum);
  for (const control of await controls.all()) {
    if (await control.isVisible()) await expect.soft(control).toBeDisabled();
  }
}

async function viewerEntry(
  page: Page,
  entry: string,
  scheme: string,
  surface = 'maintenance-shell'
) {
  const query = new URLSearchParams({
    surface,
    sprint: 's1',
    role: 'viewer',
    work_access: 'reader',
    scheme,
    entry
  });
  await page.goto(`/playwright/s0-native.html?${query}`, {
    waitUntil: 'domcontentloaded'
  });
  if (surface !== 'machine')
    await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
}

async function presentPermissions(
  page: Page,
  permissions: Record<string, string[]>
) {
  // The native user endpoint supplies this dictionary; keep CRUD role=view.
  await page.evaluate(async (permissions) => {
    const modulePath = '/src/states/UserState.tsx';
    const { useUserState } = await import(modulePath);
    const state = useUserState.getState();
    state.setUser({ ...state.user, permissions });
  }, permissions);
}

for (const permission of ['capture', 'reconcile'] as const) {
  test(`specialized ${permission} permission gates custody actions independently`, async ({
    page
  }, info) => {
    const writes = watchWrites(page);
    await closeoutWork(page);
    await page.route(
      '**/api/tasks/work-orders/73/closeout/part-usage/',
      (route) =>
        route.fulfill({
          json: [
            {
              id: 81,
              work_order: 73,
              source: 'narrative',
              candidate_text: 'P32 retained candidate',
              state: 'variance_open',
              disposition: '',
              variance_reason: '',
              version: 1,
              planned_quantity: null,
              issued_quantity: null,
              used_quantity: null
            }
          ]
        })
    );
    await viewerEntry(
      page,
      '/maintenance/work-orders/73/?work_panel=closeout',
      'light'
    );
    await presentPermissions(
      page,
      permission === 'capture'
        ? { closeout: ['capture'] }
        : { closeout_parts: ['reconcile'] }
    );
    const editor = page.getByTestId('closeout-capture-disclosure');
    await editor
      .getByText('Edit or add to the capture', { exact: true })
      .click();
    await editor.getByText('Parts', { exact: true }).click();
    if (permission === 'reconcile') {
      await editor.getByPlaceholder('Disposition', { exact: true }).click();
      await page
        .getByRole('option', { name: 'dismissed', exact: true })
        .click();
    } else {
      await expect(
        editor.getByPlaceholder('Disposition', { exact: true })
      ).toBeDisabled();
    }
    const refresh = editor.getByRole('button', {
      name: 'Refresh from custody truth',
      exact: true
    });
    const resolve = editor.getByRole('button', {
      name: 'Resolve',
      exact: true
    });
    if (permission === 'capture') {
      await expect(refresh).toBeEnabled();
      await expect(resolve).toBeDisabled();
    } else {
      await expect(refresh).toBeDisabled();
      await expect(resolve).toBeEnabled();
    }
    await expectNoPageOverflow(page);
    await capturePanel(page, info.outputPath(`specialized-${permission}.png`));
    expect(writes).toEqual([]);
  });
}

test('specialized review and complete permissions do not require a CRUD change role', async ({
  page
}, info) => {
  const writes = watchWrites(page);
  await closeoutWork(page);
  await viewerEntry(
    page,
    '/maintenance/work-orders/73/?work_panel=closeout',
    'dark'
  );
  await presentPermissions(page, {
    closeout: ['review'],
    workorder: ['complete']
  });
  await expect(
    page.getByRole('button', { name: 'Update maintenance record', exact: true })
  ).toBeEnabled();
  const editor = page.getByTestId('closeout-capture-disclosure');
  await editor.getByText('Edit or add to the capture', { exact: true }).click();
  await editor.getByText('Review', { exact: true }).click();
  await expect(
    editor.getByRole('button', { name: 'Record decisions', exact: true })
  ).toBeEnabled();
  await capturePanel(page, info.outputPath('specialized-review-complete.png'));
  expect(writes).toEqual([]);
});

test('extracting an existing open capture requires capture permission', async ({
  page
}, info) => {
  const writes = watchWrites(page);
  await closeoutWork(page, { captures: [{ ...story, status: 'open' }] });
  await viewerEntry(
    page,
    '/maintenance/work-orders/73/?work_panel=closeout',
    'dark'
  );
  const editor = page.getByTestId('closeout-capture-disclosure');
  // An unfinished saved capture opens its recovery disclosure automatically.
  await expect(editor).toHaveAttribute('open', '');
  await editor.getByText('Narrative', { exact: true }).click();
  const extract = editor.getByRole('button', {
    name: 'Extract structured draft',
    exact: true
  });
  // Prove the saved capture has finished loading before isolating the role.
  await presentPermissions(page, { closeout: ['capture'] });
  await expect(extract).toBeEnabled();
  await presentPermissions(page, {});
  await expect(extract).toBeDisabled();
  await extract.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath('viewer-saved-capture-locked-extract.png'),
    animations: 'disabled'
  });
  await presentPermissions(page, { closeout: ['capture'] });
  await expect(extract).toBeEnabled();
  expect(writes).toEqual([]);
});

test('viewer can keep a local narrative while capture and dictation require their own permission', async ({
  page
}, info) => {
  const writes = watchWrites(page);
  await closeoutWork(page, { captures: [] });
  await viewerEntry(
    page,
    '/maintenance/work-orders/73/?work_panel=closeout',
    'light'
  );
  const editor = page.getByTestId('closeout-capture-disclosure');
  await editor.getByText('Edit or add to the capture', { exact: true }).click();
  const narrative = editor.getByRole('textbox', {
    name: 'What did you do?',
    exact: true
  });
  const reason = editor.getByRole('textbox', {
    name: 'Why voice is unavailable or unusable',
    exact: true
  });
  await narrative.fill('P32 synthetic unsaved local narrative');
  await reason.fill('P32 synthetic unavailable microphone');
  await expect(
    editor.getByRole('button', { name: 'Capture narrative', exact: true })
  ).toBeDisabled();
  await narrative.fill('');
  await expect(
    editor.getByRole('button', { name: 'Dictate', exact: true })
  ).toBeDisabled();
  await narrative.fill('P32 synthetic unsaved local narrative');
  await narrative.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath('viewer-local-narrative-locked-save.png'),
    animations: 'disabled'
  });
  await presentPermissions(page, { closeout: ['capture'] });
  await expect(
    editor.getByRole('button', { name: 'Capture narrative', exact: true })
  ).toBeEnabled();
  await narrative.fill('');
  await expect(
    editor.getByRole('button', { name: 'Dictate', exact: true })
  ).toBeEnabled();
  expect(writes).toEqual([]);
});

test('complete-only authority can type a manual account without capture or review permission', async ({
  page
}, info) => {
  const writes = watchWrites(page);
  await closeoutWork(page, { captures: [] });
  await viewerEntry(
    page,
    '/maintenance/work-orders/73/?work_panel=closeout',
    'dark'
  );
  await presentPermissions(page, { workorder: ['complete'] });
  const editor = page.getByTestId('closeout-capture-disclosure');
  await editor.getByText('Edit or add to the capture', { exact: true }).click();
  await editor.getByText('Review', { exact: true }).click();
  for (const [name, value] of [
    ['Action taken', 'P32 synthetic manual action'],
    ['Result', 'P32 synthetic recorded result'],
    ['Verification summary', 'P32 synthetic verification account']
  ])
    await editor.getByRole('textbox', { name, exact: true }).fill(value);
  await expect(
    page.getByRole('button', { name: 'Update maintenance record', exact: true })
  ).toBeEnabled();
  await expect(
    editor.getByRole('button', { name: 'Record decisions', exact: true })
  ).toHaveCount(0);
  await capturePanel(page, info.outputPath('complete-only-manual-account.png'));
  await editor
    .getByRole('textbox', { name: 'Verification summary', exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath('complete-only-manual-fields.png'),
    animations: 'disabled'
  });
  expect(writes).toEqual([]);
});

for (const grant of ['review-only', 'viewer'] as const) {
  for (const scheme of ['light', 'dark']) {
    test(`no-capture account and completion fields require complete permission (${grant}, ${scheme})`, async ({
      page
    }, info) => {
      const writes = watchWrites(page);
      await closeoutWork(page, { captures: [] });
      await viewerEntry(
        page,
        '/maintenance/work-orders/73/?work_panel=closeout',
        scheme
      );
      await presentPermissions(page, { workorder: ['complete'] });
      const editor = page.getByTestId('closeout-capture-disclosure');
      await editor
        .getByText('Edit or add to the capture', { exact: true })
        .click();
      await editor.getByText('Review', { exact: true }).click();
      const action = editor.getByRole('textbox', {
        name: 'Action taken',
        exact: true
      });
      const downtime = page.getByRole('textbox', {
        name: 'Downtime (minutes)',
        exact: true
      });
      const followUp = page.getByRole('checkbox', {
        name: 'Follow-up required',
        exact: true
      });
      // Prove loading has ended and these controls can be used before revoking only complete.
      await expect(action).toBeEnabled();
      await expect(downtime).toBeEnabled();
      await expect(followUp).toBeEnabled();
      await presentPermissions(
        page,
        grant === 'review-only' ? { closeout: ['review'] } : {}
      );
      await expect(action).toBeDisabled();
      await expect(downtime).toBeDisabled();
      await expect(followUp).toBeDisabled();
      await action.scrollIntoViewIfNeeded();
      await page.screenshot({
        path: info.outputPath(`no-capture-${grant}-${scheme}-account.png`),
        animations: 'disabled'
      });
      await downtime.scrollIntoViewIfNeeded();
      await page.screenshot({
        path: info.outputPath(`no-capture-${grant}-${scheme}-completion.png`),
        animations: 'disabled'
      });
      await expectNoPageOverflow(page);
      expect(writes).toEqual([]);
    });
  }
}

test('capture-only authority cannot review decisions or complete an otherwise eligible account', async ({
  page
}, info) => {
  const writes = watchWrites(page);
  await closeoutWork(page);
  await viewerEntry(
    page,
    '/maintenance/work-orders/73/?work_panel=closeout',
    'light'
  );
  await presentPermissions(page, {
    closeout: ['review'],
    workorder: ['complete']
  });
  const update = page.getByRole('button', {
    name: 'Update maintenance record',
    exact: true
  });
  await expect(update).toBeEnabled();
  const editor = page.getByTestId('closeout-capture-disclosure');
  await editor.getByText('Edit or add to the capture', { exact: true }).click();
  await editor.getByText('Review', { exact: true }).click();
  const decisions = editor.getByRole('button', {
    name: 'Record decisions',
    exact: true
  });
  await expect(decisions).toBeEnabled();
  // Revoke only these grants after proving account and decision eligibility.
  await presentPermissions(page, { closeout: ['capture'] });
  await expect(update).toBeDisabled();
  await expect(
    editor.getByRole('textbox', { name: 'Action taken', exact: true })
  ).toBeDisabled();
  await expect(decisions).toBeDisabled();
  await capturePanel(
    page,
    info.outputPath('capture-only-no-review-or-complete.png')
  );
  await editor
    .getByRole('button', { name: 'Record decisions', exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath('capture-only-locked-decisions.png'),
    animations: 'disabled'
  });
  expect(writes).toEqual([]);
});

for (const pump of [false, true]) {
  for (const scheme of ['light', 'dark']) {
    test(`mimic alarm permissions follow native change and add roles (${pump ? 'pump' : 'station'}, ${scheme})`, async ({
      page
    }, info) => {
      const writes = watchWrites(page);
      await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
        route.fulfill({
          json: {
            ...stationMimic(BAYS),
            selected_unit: pump ? 'P1' : null,
            alarms: [
              {
                pointer: '/p32/alarm',
                label: 'P32 synthetic warning',
                group: 'Motor',
                value: 7,
                unit: 'degC',
                quality: 'good',
                observed_at: '2026-10-19T12:00:00Z',
                age_seconds: 2,
                reason: null,
                condition: 'warning',
                thresholds_configured: true,
                machine: 18,
                anomaly: 44,
                anomaly_status: 'open',
                severity: 'warning'
              }
            ]
          }
        })
      );
      const query = new URLSearchParams({
        surface: 'mimic',
        role: 'viewer',
        work_access: 'reader',
        station: '17',
        scheme,
        ...(pump ? { pump: 'P1' } : {})
      });
      await page.goto(`/playwright/s0-native.html?${query}`);
      const alarm = page.locator('tr[data-anomaly="44"]');
      await expect(alarm).toContainText('P32 synthetic warning');
      for (const name of ['Acknowledge', 'Create repair', 'Dismiss'])
        await expect(
          alarm.getByRole('button', { name: new RegExp(`^${name}\\b`) })
        ).toHaveCount(0);
      await expectNoPageOverflow(page);
      await capturePanel(
        page,
        info.outputPath(
          `mimic-${pump ? 'pump' : 'station'}-viewer-${scheme}.png`
        )
      );
      await page.screenshot({
        path: info.outputPath(
          `mimic-${pump ? 'pump' : 'station'}-viewer-actions-${scheme}.png`
        ),
        animations: 'disabled'
      });
      for (const grants of [['add'], ['change'], ['change', 'add']]) {
        await page.evaluate(async (grants) => {
          const modulePath = '/src/states/UserState.tsx';
          const { useUserState } = await import(modulePath);
          const state = useUserState.getState();
          state.setUser({
            ...state.user,
            roles: { work_order: ['view', ...grants] }
          });
        }, grants);
        for (const name of ['Acknowledge', 'Create repair', 'Dismiss']) {
          const control = alarm.getByRole('button', {
            name: new RegExp(`^${name}\\b`)
          });
          if (grants.includes(name === 'Create repair' ? 'add' : 'change')) {
            await expect(control).toBeEnabled();
            await control.scrollIntoViewIfNeeded();
            await expect(control).toBeInViewport({ ratio: 1 });
            if (info.project.name.endsWith('phone')) {
              const box = (await control.boundingBox())!;
              expect(box.width).toBeGreaterThanOrEqual(44);
              expect(box.height).toBeGreaterThanOrEqual(44);
            }
          } else await expect(control).toHaveCount(0);
        }
        if (grants.includes('change') && grants.includes('add')) {
          // Position the authorized action cell in both scroll axes: scrolling
          // only the last button can leave its neighbours clipped on a phone.
          await alarm
            .locator('td')
            .last()
            .evaluate((cell) =>
              cell.scrollIntoView({ block: 'center', inline: 'center' })
            );
          for (const name of ['Acknowledge', 'Create repair', 'Dismiss'])
            await expect(
              alarm.getByRole('button', {
                name: new RegExp(`^${name}\\b`)
              })
            ).toBeInViewport({ ratio: 1 });
        }
      }
      await expectNoPageOverflow(page);
      await alarm.screenshot({
        path: info.outputPath(
          `mimic-${pump ? 'pump' : 'station'}-authorized-actions-${scheme}.png`
        )
      });
      expect(writes).toEqual([]);
    });
  }
}

for (const scheme of ['light', 'dark'] as const) {
  test(`viewer Work and Verify keep command controls locked (${scheme})`, async ({
    page
  }, info) => {
    const writes = watchWrites(page);
    await mockExecutionWork(page, continuityWork(false));
    await page.route(
      '**/api/tasks/work-orders/73/closeout/readings/',
      (route) => route.fulfill({ json: [] })
    );
    await openExecutionPanel(page, 'work', scheme);
    await revealWorkContext(page, 'Continuity and handover');
    await expect(page.getByTestId('work-continuity')).toBeVisible();
    await noWriteControls(
      page,
      /Hold work record|Resume work record|Accept assignment|Generate|\bAssign\b|Update record/
    );
    await expectNoPageOverflow(page);
    await capturePanel(page, info.outputPath(`viewer-work-${scheme}.png`));
    await page
      .getByRole('tab', { name: 'Verify & Return', exact: true })
      .click();
    await noWriteControls(
      page,
      /Add reading|Record reading|Verify|Accept|Reject/
    );
    await capturePanel(page, info.outputPath(`viewer-verify-${scheme}.png`));
    expect(writes).toEqual([]);
  });

  test(`viewer Closeout cannot capture review reconcile or update (${scheme})`, async ({
    page
  }, info) => {
    const writes = watchWrites(page);
    await closeoutWork(page, { captures: [] });
    await viewerEntry(
      page,
      '/maintenance/work-orders/73/?work_panel=closeout',
      scheme
    );
    await expect(page.getByTestId('prepared-closeout')).toBeVisible();
    await noWriteControls(
      page,
      /Start voice-transcript capture|Use typed story fallback|Save|Update maintenance record|Accept|Reject|Reconcile|Extract|Record reading/
    );
    await expectNoPageOverflow(page);
    await capturePanel(page, info.outputPath(`viewer-closeout-${scheme}.png`));
    expect(writes).toEqual([]);
  });

  test(`viewer Health cannot acknowledge dismiss analyze or create repair (${scheme})`, async ({
    page
  }, info) => {
    const writes = watchWrites(page);
    await page.addInitScript(() => {
      const url = new URL(window.location.href);
      url.searchParams.set('role', 'viewer');
      window.history.replaceState(null, '', url);
    });
    await openHealth(page, {
      scheme,
      anomalies: [anomaly()],
      summary: {
        state: 'warning',
        anomaly_counts: { critical: 0, warning: 1, info: 0 },
        active_anomaly_count: 1
      }
    });

    await expect(
      page.getByRole('heading', { name: 'Signals', exact: true })
    ).toBeVisible();
    await expect(page.getByText(/^View only:/)).toBeVisible();
    await expect(
      page.getByRole('button', {
        name: /^(Analyze|Acknowledge|Dismiss|Create repair|Start repair)\b/
      })
    ).toHaveCount(0);
    await expectNoPageOverflow(page);
    await capturePanel(page, info.outputPath(`viewer-health-${scheme}.png`));
    expect(writes).toEqual([]);
  });

  test(`viewer job Parts reads requirements without write controls or stock links (${scheme})`, async ({
    page
  }, info) => {
    const writes = watchWrites(page);
    await mockExecutionWork(page, continuityWork());
    await page.route('**/api/tasks/work-orders/73/materials/', (route) =>
      route.fulfill({
        json: {
          work_order: 73,
          source: 'requirements',
          kits_enabled: false,
          stock_visible: false,
          component: null,
          requirements: [
            {
              key: 'p32-line',
              part: {
                pk: 5,
                name: 'P32 synthetic seal kit',
                ipn: 'P32-SEAL',
                units: 'ea',
                virtual: false
              },
              requested_part: null,
              quantity: 2,
              required: true,
              fit_basis: null,
              reservations: [],
              shortages: [],
              availability_check: null
            }
          ],
          unmatched_usage: []
        }
      })
    );
    await viewerEntry(
      page,
      '/maintenance/work-orders/73/?work_panel=parts',
      scheme
    );
    const line = page.locator('[data-requirement="p32-line"]');
    await expect(line).toContainText('P32 synthetic seal kit');
    await expect(line).toContainText('Required 2 ea');
    await expect(line).toContainText('Not recorded for this requirement');
    await expect(line.getByRole('link')).toHaveCount(0);
    await noWriteControls(
      page,
      /Reserve|Issue|Consume|Return|Allocate|Add part/
    );
    await expectNoPageOverflow(page);
    await capturePanel(page, info.outputPath(`viewer-job-parts-${scheme}.png`));
    expect(writes).toEqual([]);
  });

  test(`viewer machine Parts reads components without write controls or stock links (${scheme})`, async ({
    page
  }, info) => {
    const writes = watchWrites(page);
    await mockMachine(page);
    await page.route('**/api/assets/registry/18/components/**', (route) =>
      route.fulfill({
        json: {
          count: 1,
          results: [
            {
              pk: 1,
              uuid: '00000000-0000-4000-8000-000000000001',
              machine: 18,
              machine_name: 'Synthetic pump',
              part: 501,
              part_name: 'P32 synthetic motor',
              virtual: false,
              code: 'PS-MOTOR:1',
              name: 'P32 synthetic motor',
              status: 'draft',
              provenance: 'P32 synthetic inferred assignment; fit unverified.',
              provenance_display:
                'P32 synthetic inferred assignment; fit unverified.',
              provenance_imported_at: null,
              review_note: '',
              reviewed_at: null,
              reference_position: 'motor',
              open_work: null,
              stock: null
            }
          ]
        }
      })
    );
    await page.route('**/api/assets/parts/?*', (route) =>
      route.fulfill({ json: { count: 0, results: [], next: null } })
    );
    await viewerEntry(page, '/machines/machine/18/parts/', scheme, 'machine');
    const row = page.getByRole('row').filter({ hasText: 'PS-MOTOR:1' });
    await expect(
      row.getByLabel('Draft: slot assignment not verified', { exact: true })
    ).toHaveText('Draft');
    await expect(row).toContainText(
      'P32 synthetic inferred assignment; fit unverified.'
    );
    await expect(
      row.getByRole('img', { name: 'Work could not be checked', exact: true })
    ).toHaveText('—');
    await expect(
      row.getByRole('img', {
        name: 'Fitted identity could not be read.',
        exact: true
      })
    ).toHaveText('—');
    await expect(row.getByRole('link')).toHaveCount(0);
    await expect(
      page.getByRole('columnheader', { name: 'Catalogue stock' })
    ).toHaveCount(0);
    await noWriteControls(
      page,
      /Add|Propose|Verify|Approve|Delete|Create repair|Start repair/
    );
    await expectNoPageOverflow(page);
    await capturePanel(
      page,
      info.outputPath(`viewer-machine-parts-${scheme}.png`)
    );
    expect(writes).toEqual([]);
  });
}

for (const status of [403, 404]) {
  test(`Closeout keeps refused ${status} sources unavailable and locked`, async ({
    page
  }, info) => {
    const writes = watchWrites(page);
    await closeoutWork(page);
    await page.route(
      '**/api/tasks/work-orders/73/closeout/captures/',
      (route) =>
        route.fulfill({ status, json: { detail: 'P32 synthetic refusal' } })
    );
    await viewerEntry(
      page,
      '/maintenance/work-orders/73/?work_panel=closeout',
      status === 403 ? 'light' : 'dark'
    );
    await expect(
      page.getByRole('alert').filter({
        hasText:
          'Closeout source unavailable. Last-known values and drafts are retained; saving is locked.'
      })
    ).toBeVisible();
    await noWriteControls(
      page,
      /Start voice-transcript capture|Use typed story fallback|Record decisions|Save|Update maintenance record/
    );
    await expect(
      page.getByText('Update confirmed', { exact: true })
    ).toHaveCount(0);
    await expectNoPageOverflow(page);
    await capturePanel(page, info.outputPath(`closeout-refused-${status}.png`));
    expect(writes).toEqual([]);
  });

  test(`Work refuses ${status} without an empty or completed projection`, async ({
    page
  }, info) => {
    const writes = watchWrites(page);
    await mockWorkList(page);
    await page.route('**/api/kanban/work-orders/73/overview/', (route) =>
      route.fulfill({ status, json: { detail: 'P32 synthetic refusal' } })
    );
    await viewerEntry(
      page,
      '/maintenance/work-orders/73/?work_panel=work',
      'dark'
    );
    await expect(
      page.getByText('Work order unavailable', { exact: true })
    ).toBeVisible();
    await expect(
      page.getByText(
        'This work order does not exist or is outside your access scope.',
        { exact: true }
      )
    ).toBeVisible();
    await expect(page.getByTestId('work-continuity')).toHaveCount(0);
    await expectNoPageOverflow(page);
    await capturePanel(page, info.outputPath(`work-refused-${status}.png`));
    expect(writes).toEqual([]);
  });

  test(`job Parts refuses ${status} without zero requirements`, async ({
    page
  }, info) => {
    const writes = watchWrites(page);
    await mockExecutionWork(page, continuityWork());
    await page.route('**/api/tasks/work-orders/73/materials/', (route) =>
      route.fulfill({ status, json: { detail: 'P32 synthetic refusal' } })
    );
    await viewerEntry(
      page,
      '/maintenance/work-orders/73/?work_panel=parts',
      'dark'
    );
    const panel = page.getByRole('tabpanel', { name: 'Parts', exact: true });
    await expect(
      panel
        .getByText(
          status === 404
            ? /not available|outside|disabled/i
            : /could not|cannot|unavailable|access/i
        )
        .first()
    ).toBeVisible();
    await expect(panel).not.toContainText('No material requirements');
    await noWriteControls(
      page,
      /Reserve|Issue|Consume|Return|Allocate|Add part/
    );
    await expectNoPageOverflow(page);
    await capturePanel(
      page,
      info.outputPath(`job-parts-refused-${status}.png`)
    );
    expect(writes).toEqual([]);
  });

  test(`machine Parts refuses ${status} without empty catalogue context`, async ({
    page
  }, info) => {
    const writes = watchWrites(page);
    await mockMachine(page);
    await page.route('**/api/assets/registry/18/components/**', (route) =>
      route.fulfill({ status, json: { detail: 'P32 synthetic refusal' } })
    );
    await page.route('**/api/assets/parts/?*', (route) =>
      route.fulfill({ json: { count: 0, results: [], next: null } })
    );
    await viewerEntry(page, '/machines/machine/18/parts/', 'light', 'machine');
    await expect(
      page.getByRole('alert', { name: 'Error Details', exact: true })
    ).toContainText(`Request failed with status code ${status}`);
    // The native notification can cover Error Details on a 390px phone.
    for (const close of await page
      .locator('.mantine-Notification-closeButton')
      .all())
      await close.click();
    await expect(page.locator('.mantine-Notification-root')).toHaveCount(0);
    await noWriteControls(page, /Add|Propose|Verify|Approve|Delete/);
    await expectNoPageOverflow(page);
    await capturePanel(
      page,
      info.outputPath(`machine-parts-refused-${status}.png`)
    );
    expect(writes).toEqual([]);
  });

  test(`Health keeps ${status} signals and anomalies unavailable`, async ({
    page
  }, info) => {
    const writes = watchWrites(page);
    await page.addInitScript(() => {
      const url = new URL(window.location.href);
      url.searchParams.set('role', 'viewer');
      window.history.replaceState(null, '', url);
    });
    await openHealth(page, {
      signalsStatus: status,
      anomaliesStatus: status,
      snapshotsStatus: status,
      scheme: 'dark'
    });

    await expect(
      page
        .getByText(
          /Signals could not be read|Signal readings unavailable|Unable to load signals/i
        )
        .first()
    ).toBeVisible();
    await expect(
      page.getByRole('table', { name: 'Mapped signal readings' })
    ).toHaveCount(0);
    await expect(
      page.getByText('No active anomalies.', { exact: true })
    ).toHaveCount(0);
    await expect(
      page.getByRole('button', {
        name: /^(Analyze|Acknowledge|Dismiss|Create repair|Start repair)\b/
      })
    ).toHaveCount(0);
    await capturePanel(page, info.outputPath(`health-refused-${status}.png`));
    expect(writes).toEqual([]);
  });

  test(`Health refuses ${status} summary without a healthy or zero state`, async ({
    page
  }, info) => {
    const writes = watchWrites(page);
    await mockMachine(page);
    await page.route('**/api/machine-health/machines/18/health/**', (route) =>
      route.fulfill({ status, json: { detail: 'P32 synthetic refusal' } })
    );
    await viewerEntry(
      page,
      '/machines/machine/18/health/',
      status === 403 ? 'light' : 'dark',
      'machine'
    );
    await expect(
      page.getByRole('alert', { name: 'Health data unavailable', exact: true })
    ).toBeVisible();
    await expect(page.getByText('Normal', { exact: true })).toHaveCount(0);
    await expect(page.getByText(/^(Critical|Warning|Info): 0$/)).toHaveCount(0);
    await expect(
      page.getByText('No active anomalies.', { exact: true })
    ).toHaveCount(0);
    await expect(
      page.getByRole('button', {
        name: /^(Analyze|Acknowledge|Dismiss|Create repair|Start repair)\b/
      })
    ).toHaveCount(0);
    await expectNoPageOverflow(page);
    await capturePanel(
      page,
      info.outputPath(`health-summary-refused-${status}.png`)
    );
    expect(writes).toEqual([]);
  });
}

for (const attribution of ['customer', 'unattributed'] as const) {
  for (const scheme of ['light', 'dark'] as const) {
    test(`O24 authorized ${attribution} Work response renders for a viewer in ${scheme}`, async ({
      page
    }, info) => {
      const writes = watchWrites(page);
      const work = continuityWork();
      // The overview does not expose customer IDs. Backend scope tests prove
      // authorization; this fixture proves an authorized response renders for
      // a viewer without granting execution actions in either O24 case.
      work.title = `Synthetic ${attribution} Work`;
      if (attribution === 'customer') {
        work.company = 'Synthetic customer';
        work.machine_name = 'Synthetic foreign client pump';
      } else {
        work.machine = null;
        work.machine_name = null;
        work.machine_location = null;
      }
      await mockExecutionWork(page, work);
      await viewerEntry(
        page,
        '/maintenance/work-orders/73/?work_panel=work',
        scheme
      );
      await expect(
        page.getByRole('heading', {
          name: `WO-73: Synthetic ${attribution} Work`,
          exact: true
        })
      ).toBeVisible();
      await expect(page.getByTestId('work-continuity')).toBeVisible();
      await expect(
        page.getByText(
          'No hold/resume action is available for this Work and your current permission.',
          { exact: true }
        )
      ).toBeVisible();
      await noWriteControls(page, /Hold work|Resume work|Accept handover/);
      await expectNoPageOverflow(page);
      await capturePanel(
        page,
        info.outputPath(`o24-${attribution}-${scheme}.png`)
      );
      await page.screenshot({
        path: info.outputPath(`o24-${attribution}-${scheme}-viewport.png`),
        animations: 'disabled'
      });
      expect(writes).toEqual([]);
    });
  }
}
