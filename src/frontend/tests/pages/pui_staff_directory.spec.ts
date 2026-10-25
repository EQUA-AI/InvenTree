import { type Page, expect, test } from '@playwright/test';
import { auditAccessibility } from '../s0/accessibility';

const plumber = {
  user_id: 12,
  display_name: 'Alex Lee',
  job_title: 'Plumber',
  trades: [{ pk: 1, code: 'plumbing', name: 'Plumbing', active: true }]
};
const electrician = {
  user_id: 21,
  display_name: 'Alex Lee',
  job_title: null,
  trades: [{ pk: 2, code: 'electrical', name: 'Electrical', active: true }]
};

async function mockApi(page: Page) {
  let persisted = {
    id: 40,
    lifecycle_version: 3,
    assigned_to: null as number | null,
    assigned_to_name: null as string | null,
    assignee: 'Legacy contractor',
    required_trades: ['plumbing'],
    trade_requirements_confirmed: true
  };
  await page.route('**/api/user/trades/**', (route) =>
    route.fulfill({
      json: { count: 2, results: [...plumber.trades, ...electrician.trades] }
    })
  );
  await page.route('**/api/user/staff-directory/?**', (route) => {
    const url = new URL(route.request().url());
    const userId = url.searchParams.get('user_id');
    const people = [plumber, electrician];
    return route.fulfill({
      json: {
        count: userId ? 1 : 2,
        results: userId
          ? people.filter((person) => String(person.user_id) === userId)
          : people
      }
    });
  });
  await page.route('**/api/kanban/work-orders/40/', (route) =>
    route.fulfill({ json: persisted })
  );
  await page.route('**/api/user/staff-directory/assignment/', (route) => {
    const command = route.request().postDataJSON();
    persisted = {
      ...persisted,
      lifecycle_version: persisted.lifecycle_version + 1,
      ...(!command.kanban_card_id
        ? {
            assigned_to: command.assigned_to,
            assigned_to_name:
              [plumber, electrician].find(
                (person) => person.user_id === command.assigned_to
              )?.display_name ?? null
          }
        : {}),
      ...(command.required_trades !== undefined
        ? {
            required_trades: command.required_trades,
            trade_requirements_confirmed: command.trade_requirements_confirmed
          }
        : {})
    };
    return route.fulfill({ json: { work_order_id: 40 } });
  });
}

async function selectStaff(page: Page, id: number) {
  await page
    .getByRole('combobox', { name: /^(Assigned staff|Assignee)$/, exact: true })
    .click();
  await page.getByRole('option', { name: new RegExp(`#${id}\\)`) }).click();
}

for (const viewport of [
  { width: 1440, height: 1000 },
  { width: 390, height: 844 }
]) {
  test(`merged creation stays accessible and contained at ${viewport.width}px`, async ({
    page
  }, info) => {
    await page.setViewportSize(viewport);
    await mockApi(page);
    await page.goto('/playwright/staff-directory.html?mode=create');
    await page
      .getByRole('textbox', { name: 'Title', exact: true })
      .fill('Review pipe leak');
    await page
      .getByRole('combobox', { name: 'Required trades', exact: true })
      .click();
    await page.getByRole('option', { name: 'Plumbing', exact: true }).click();
    await page.keyboard.press('Escape');
    await page
      .getByRole('checkbox', {
        name: 'I have reviewed and confirmed these trade requirements'
      })
      .check();
    await selectStaff(page, 12);
    const dialog = page.getByRole('dialog', {
      name: 'New work order',
      exact: true
    });
    const submit = page.getByRole('button', {
      name: 'Create work order',
      exact: true
    });
    await expect(submit).toBeEnabled();
    await auditAccessibility(
      page,
      dialog,
      info,
      `integrated-create-${viewport.width}`
    );
    await page
      .getByRole('textbox', { name: 'Title', exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: info.outputPath('integrated-create-top.png'),
      fullPage: true,
      animations: 'disabled'
    });
    await submit.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: info.outputPath('integrated-create-actions.png'),
      fullPage: true,
      animations: 'disabled'
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1
      )
    ).toBe(true);
  });
}

test('duplicate names remain ID-backed; mismatch requires a reason and explicit human confirmation', async ({
  page
}) => {
  await mockApi(page);
  await page.goto('/playwright/staff-directory.html?override=1');
  await selectStaff(page, 21);
  await page
    .getByRole('textbox', { name: 'Assignment reason', exact: true })
    .fill('Reviewed work scope');
  const save = page.getByRole('button', {
    name: 'Override and save assignment'
  });
  await expect(save).toBeDisabled();
  await page
    .getByRole('textbox', { name: 'Override reason', exact: true })
    .fill('Supervised exception');
  await expect(save).toBeDisabled();
  await page
    .getByRole('checkbox', {
      name: 'I explicitly authorize this trade mismatch override'
    })
    .check();
  const request = page.waitForRequest(
    '**/api/user/staff-directory/assignment/'
  );
  await save.click();
  expect((await request).postDataJSON()).toMatchObject({
    work_order_id: 40,
    assigned_to: 21,
    expected_version: 3,
    reason: 'Reviewed work scope',
    override_reason: 'Supervised exception',
    required_trades: ['plumbing'],
    trade_requirements_confirmed: true
  });
  await expect(page.getByText('Saved successfully')).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole('combobox', { name: 'Assignee', exact: true })
  ).toHaveValue(/#21/);
});

test('a directory reader cannot self-grant a trade override', async ({
  page
}) => {
  await mockApi(page);
  await page.goto('/playwright/staff-directory.html');
  await selectStaff(page, 21);
  await expect(
    page.getByText('You do not have permission to override a trade mismatch.')
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Override and save assignment' })
  ).toBeDisabled();
  await expect(
    page.getByRole('textbox', { name: 'Override reason', exact: true })
  ).toHaveCount(0);
});

test('clearing a card override posts null and its real card ID to restore inheritance', async ({
  page
}) => {
  await mockApi(page);
  await page.goto('/playwright/staff-directory.html?card=1');
  await expect(
    page.getByRole('combobox', { name: 'Assignee', exact: true })
  ).toHaveValue(/#12/);
  await page
    .getByRole('button', { name: 'Clear assignee', exact: true })
    .click();
  await page
    .getByRole('textbox', { name: 'Assignment reason', exact: true })
    .fill('Restore work-order owner');
  const request = page.waitForRequest(
    '**/api/user/staff-directory/assignment/'
  );
  await page
    .getByRole('button', { name: 'Save assignment', exact: true })
    .click();
  expect((await request).postDataJSON()).toMatchObject({
    assigned_to: null,
    kanban_card_id: 81,
    expected_version: 3
  });
});

test('summary is keyboard-accessible, legacy text has no fabricated popup, revocation removes cached titles', async ({
  page
}) => {
  await mockApi(page);
  await page.goto('/playwright/staff-directory.html?mode=summary');
  const target = page.getByRole('button', {
    name: 'Staff details: Alex Lee',
    exact: true
  });
  await target.focus();
  await target.press('Enter');
  await expect(
    page.getByRole('dialog', { name: 'Staff details: Alex Lee', exact: true })
  ).toContainText('Plumber');
  await page.keyboard.press('Escape');
  await expect(target).toBeFocused();
  await expect(
    page.getByRole('button', { name: /Legacy contractor/ })
  ).toHaveCount(0);
  await page.getByRole('button', { name: 'Revoke permissions' }).click();
  await expect(target).toHaveCount(0);
  await expect(page.getByText('Plumber', { exact: true })).toHaveCount(0);
});

test('denied directory permission never requests personnel and lookup errors are not empty matches', async ({
  page
}) => {
  let calls = 0;
  await mockApi(page);
  page.on('request', (request) => {
    if (request.url().includes('/api/user/staff-directory/?')) calls++;
  });
  await page.goto('/playwright/staff-directory.html?denied=1');
  await expect(
    page.getByText('Staff directory access is required to select personnel.')
  ).toBeVisible();
  expect(calls).toBe(0);
  await page.route('**/api/user/staff-directory/?**', (route) =>
    route.fulfill({ status: 403, json: { detail: 'Not authorized' } })
  );
  await page.goto('/playwright/staff-directory.html');
  await expect(
    page.getByText(
      'The staff directory could not be loaded. This is not an empty candidate list.'
    )
  ).toBeVisible();
});

test('administrators edit occupational data through the narrow endpoint only', async ({
  page
}) => {
  await mockApi(page);
  let persisted = { position: 'Maintenance technician', trades: [1] };
  await page.route('**/api/user/12/occupational-profile/', (route) => {
    if (route.request().method() === 'PATCH')
      persisted = route.request().postDataJSON();
    return route.fulfill({ json: persisted });
  });
  await page.goto('/playwright/staff-directory.html?mode=profile');
  await page
    .getByRole('textbox', { name: 'Job title', exact: true })
    .fill('Lead technician');
  const request = page.waitForRequest(
    (req) =>
      req.url().includes('/user/12/occupational-profile/') &&
      req.method() === 'PATCH'
  );
  await page.getByRole('button', { name: 'Save occupational profile' }).click();
  expect((await request).postDataJSON()).toEqual({
    position: 'Lead technician',
    trades: [1]
  });
  await expect(page.getByText('Saved successfully')).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole('textbox', { name: 'Job title', exact: true })
  ).toHaveValue('Lead technician');
});

test('work-package creation submits reviewed typed personnel and trade requirements', async ({
  page
}) => {
  await mockApi(page);
  await page.route('**/api/maintenance/work-packages/create/', (route) => {
    const body = route.request().postDataJSON();
    return route.fulfill({
      json: {
        work_order_id: 40,
        work_order_reference: 'WO-40',
        repair_packet_id: 7,
        repair_packet_reference: 'RP-7',
        machine_id: body.machine_id,
        anomaly_id: body.source?.anomaly_id ?? null,
        assigned_to_id: body.planning?.assigned_to_id ?? null,
        idempotency_key: body.idempotency_key,
        lifecycle_version: 2,
        replayed: false,
        warnings: []
      }
    });
  });
  await page.goto('/playwright/staff-directory.html?mode=create');
  await page
    .getByRole('textbox', { name: 'Title', exact: true })
    .fill('Repair pipe leak');
  await page
    .getByRole('combobox', { name: 'Required trades', exact: true })
    .click();
  await page.getByRole('option', { name: 'Plumbing', exact: true }).click();
  await page.keyboard.press('Escape');
  await page
    .getByRole('checkbox', {
      name: 'I have reviewed and confirmed these trade requirements'
    })
    .check();
  await selectStaff(page, 12);
  const request = page.waitForRequest(
    '**/api/maintenance/work-packages/create/'
  );
  await page
    .getByRole('button', { name: 'Create work order', exact: true })
    .click();
  const planning = (await request).postDataJSON().planning;
  expect(planning).toMatchObject({
    assigned_to_id: 12,
    required_trades: ['plumbing'],
    trade_requirements_confirmed: true
  });
  expect(planning).not.toHaveProperty('assignee');
  await expect(page.getByText('Saved successfully')).toBeVisible();
});

test('create remains compatible for users without directory grants', async ({
  page
}) => {
  await mockApi(page);
  await page.route('**/api/maintenance/work-packages/create/', (route) => {
    const body = route.request().postDataJSON();
    return route.fulfill({
      json: {
        work_order_id: 40,
        work_order_reference: 'WO-40',
        repair_packet_id: 7,
        repair_packet_reference: 'RP-7',
        machine_id: body.machine_id,
        anomaly_id: body.source?.anomaly_id ?? null,
        assigned_to_id: body.planning?.assigned_to_id ?? null,
        idempotency_key: body.idempotency_key,
        lifecycle_version: 2,
        replayed: false,
        warnings: []
      }
    });
  });
  await page.goto('/playwright/staff-directory.html?mode=create&denied=1');
  await page
    .getByRole('textbox', { name: 'Title', exact: true })
    .fill('Inspect conveyor');
  const request = page.waitForRequest(
    '**/api/maintenance/work-packages/create/'
  );
  await page
    .getByRole('button', { name: 'Create work order', exact: true })
    .click();
  const planning = (await request).postDataJSON().planning;
  expect(planning).toHaveProperty('assignee', '');
  expect(planning).not.toHaveProperty('assigned_to');
  expect(planning).not.toHaveProperty('required_trades');
  expect(planning).not.toHaveProperty('trade_requirements_confirmed');
  await expect(page.getByText('Saved successfully')).toBeVisible();
});

test('assignment-only users do not submit plan fields or gain requirement editing', async ({
  page
}) => {
  await mockApi(page);
  await page.goto('/playwright/staff-directory.html?no-plan=1');
  await expect(
    page.getByRole('combobox', { name: 'Required trades', exact: true })
  ).toBeDisabled();
  await selectStaff(page, 12);
  await page
    .getByRole('textbox', { name: 'Assignment reason', exact: true })
    .fill('Assign the qualified plumber');
  const request = page.waitForRequest(
    '**/api/user/staff-directory/assignment/'
  );
  await page
    .getByRole('button', { name: 'Save assignment', exact: true })
    .click();
  const command = (await request).postDataJSON();
  expect(command.assigned_to).toBe(12);
  expect(command).not.toHaveProperty('required_trades');
  expect(command).not.toHaveProperty('trade_requirements_confirmed');
});

test('staff search and pagination use scoped server queries and keep duplicate IDs distinct', async ({
  page
}) => {
  await mockApi(page);
  await page.route('**/api/user/staff-directory/?**', (route) => {
    const params = new URL(route.request().url()).searchParams;
    expect(params.get('work_order_id')).toBe('40');
    const offset = Number(params.get('offset') ?? '0');
    const person = offset ? electrician : plumber;
    return route.fulfill({
      json: {
        count: 2,
        results: params.has('user_id') ? [electrician] : [person]
      }
    });
  });
  await page.goto('/playwright/staff-directory.html');
  await page.getByRole('button', { name: 'Load more staff' }).click();
  const search = page.waitForRequest(
    (req) => new URL(req.url()).searchParams.get('search') === 'Alex'
  );
  await page
    .getByRole('combobox', { name: 'Assignee', exact: true })
    .fill('Alex');
  await search;
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Load more staff' }).click();
  await page.getByRole('combobox', { name: 'Assignee', exact: true }).click();
  await expect(page.getByRole('option', { name: /#12\)/ })).toBeVisible();
  await expect(page.getByRole('option', { name: /#21\)/ })).toBeVisible();
});

test('a stale version is never silently retried with a newer version', async ({
  page
}) => {
  await mockApi(page);
  await page.route('**/api/user/staff-directory/assignment/', (route) =>
    route.fulfill({
      status: 409,
      json: { code: 'VERSION_CONFLICT', detail: 'Work order changed' }
    })
  );
  await page.goto('/playwright/staff-directory.html');
  await selectStaff(page, 12);
  await page
    .getByRole('textbox', { name: 'Assignment reason', exact: true })
    .fill('Assign after review');
  await page
    .getByRole('button', { name: 'Save assignment', exact: true })
    .click();
  await expect(
    page.getByText(
      'The work order changed. Close and reopen this dialog to review the latest assignment before trying again.'
    )
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Save assignment', exact: true })
  ).toBeDisabled();
});

test('creation trade override is a human-confirmed action and uses the compound create command', async ({
  page
}) => {
  await mockApi(page);
  await page.route('**/api/maintenance/work-packages/create/', (route) => {
    const body = route.request().postDataJSON();
    return route.fulfill({
      json: {
        work_order_id: 40,
        work_order_reference: 'WO-40',
        repair_packet_id: 7,
        repair_packet_reference: 'RP-7',
        machine_id: body.machine_id,
        anomaly_id: body.source?.anomaly_id ?? null,
        assigned_to_id: body.planning?.assigned_to_id ?? null,
        idempotency_key: body.idempotency_key,
        lifecycle_version: 2,
        replayed: false,
        warnings: []
      }
    });
  });
  await page.goto('/playwright/staff-directory.html?mode=create&override=1');
  await page
    .getByRole('textbox', { name: 'Title', exact: true })
    .fill('Repair pipe leak');
  await page
    .getByRole('combobox', { name: 'Required trades', exact: true })
    .click();
  await page.getByRole('option', { name: 'Plumbing', exact: true }).click();
  await page.keyboard.press('Escape');
  await page
    .getByRole('checkbox', {
      name: 'I have reviewed and confirmed these trade requirements'
    })
    .check();
  await selectStaff(page, 21);
  const create = page.getByRole('button', {
    name: 'Create work order',
    exact: true
  });
  await expect(create).toBeDisabled();
  await page
    .getByRole('textbox', { name: 'Override reason', exact: true })
    .fill('Supervised exception');
  await expect(create).toBeDisabled();
  await page
    .getByRole('checkbox', {
      name: 'I explicitly authorize this trade mismatch override'
    })
    .check();
  const request = page.waitForRequest(
    '**/api/maintenance/work-packages/create/'
  );
  await create.click();
  expect((await request).postDataJSON().planning).toMatchObject({
    assigned_to_id: 21,
    override_reason: 'Supervised exception'
  });
});

test('a denied staff refresh does not retain a previously cached occupational title', async ({
  page
}) => {
  await mockApi(page);
  await page.goto('/playwright/staff-directory.html?mode=summary');
  const target = page.getByRole('button', {
    name: 'Staff details: Alex Lee',
    exact: true
  });
  await target.click();
  await expect(page.getByRole('dialog')).toContainText('Plumber');
  await page.keyboard.press('Escape');
  await page.route('**/api/user/staff-directory/?**', (route) =>
    route.fulfill({ status: 403, json: { detail: 'Scope revoked' } })
  );
  await target.click();
  await expect(
    page.getByText('Staff details could not be loaded.')
  ).toBeVisible();
  await expect(page.getByRole('status')).toHaveText(
    'Staff details could not be loaded.'
  );
  await expect(page.getByText('Plumber', { exact: true })).toHaveCount(0);
});

test('the personnel picker supports keyboard-only selection of a real user ID', async ({
  page
}) => {
  await mockApi(page);
  await page.goto('/playwright/staff-directory.html');
  const picker = page.getByRole('combobox', { name: 'Assignee', exact: true });
  await picker.focus();
  await picker.press('ArrowDown');
  await expect(page.getByRole('option', { name: /#12\)/ })).toBeVisible();
  await picker.press('Enter');
  await expect(picker).toHaveValue(/#12/);
});

test.describe('touch staff summary', () => {
  test.use({ hasTouch: true });
  test('opens from a real button on touch devices', async ({ page }) => {
    await mockApi(page);
    await page.goto('/playwright/staff-directory.html?mode=summary');
    await page
      .getByRole('button', { name: 'Staff details: Alex Lee', exact: true })
      .tap();
    await expect(page.getByRole('dialog')).toContainText('Plumber');
  });
});
