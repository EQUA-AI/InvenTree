import { createApi } from './api';
import { expect, test } from './baseFixtures';
import { allaccessuser } from './defaults';
import { doCachedLogin } from './login';

/**
 * E2E coverage for the Maintenance workspace:
 * - the Board / Calendar / Timeline panel scaffold and its deep links,
 * - the pre-rename /tasks/* URLs still resolving,
 * - persisted board columns seeded under their original keys,
 * - machine required when creating a work order,
 * - the create → appears-on-board flow through the work-package command.
 */

const taskApi = () =>
  createApi({
    username: allaccessuser.username,
    password: allaccessuser.testcred
  });

const seedMachine = async (name: string) => {
  const api = await taskApi();
  const response = await api.post('assets/machines/', { data: { name } });
  expect(response.ok()).toBeTruthy();
  return name;
};

/** Create a work order scheduled for a window today, returning its title. */
const seedScheduledCard = async (title: string, machineName: string) => {
  const api = await taskApi();
  const machine = await api.post('assets/machines/', {
    data: { name: machineName }
  });
  const machineId = (await machine.json()).pk;

  const created = await api.post('kanban/cards/', {
    data: { title, status: 'backlog', priority: 'high', machine: machineId }
  });
  expect(created.ok()).toBeTruthy();
  const cardId = (await created.json()).id;

  const now = new Date();
  const start = new Date(now);
  start.setHours(10, 0, 0, 0);
  const end = new Date(now);
  end.setHours(12, 0, 0, 0);

  const patched = await api.patch(`kanban/cards/${cardId}/`, {
    data: {
      scheduled_start: start.toISOString(),
      scheduled_end: end.toISOString()
    }
  });
  expect(patched.ok()).toBeTruthy();
  return title;
};

test('Maintenance - board loads with panels and persisted columns', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, {
    user: allaccessuser,
    url: 'maintenance/board'
  });

  // The panel scaffold.
  await page.getByRole('tab', { name: 'Board', exact: true }).waitFor();
  await page.getByRole('tab', { name: 'Calendar', exact: true }).waitFor();
  await page.getByRole('tab', { name: 'Timeline', exact: true }).waitFor();

  // The four seeded columns render on the board.
  await page.getByText('Backlog').first().waitFor();
  await page.getByText('In Progress').first().waitFor();
  await page.getByText('In Review').first().waitFor();
  await page.getByText('Done').first().waitFor();

  await page.getByRole('button', { name: 'New work order' }).waitFor();
});

test('Maintenance - the bare /maintenance/ URL renders a panel', async ({
  browser
}) => {
  // This is what the nav tab, the navigation drawer and the kanbancard model
  // overview all point at. A splat child route does not match an empty
  // remainder, so without an index route this URL rendered an empty page.
  const page = await doCachedLogin(browser, {
    user: allaccessuser,
    url: 'maintenance'
  });

  await expect(page).toHaveURL(/\/maintenance\/(board|calendar|timeline)\/?$/);
  await page.getByRole('tab', { name: 'Board', exact: true }).waitFor();
});

test('Maintenance - the old /tasks/ root resolves to a panel', async ({
  browser
}) => {
  // /tasks/ redirects to the bare /maintenance/, so it regresses with it.
  const page = await doCachedLogin(browser, {
    user: allaccessuser,
    url: 'tasks'
  });

  await expect(page).toHaveURL(/\/maintenance\/(board|calendar|timeline)\/?$/);
  await page.getByRole('tab', { name: 'Board', exact: true }).waitFor();
});

test('Maintenance - old /tasks/ links still resolve', async ({ browser }) => {
  // Bookmarks captured before the rename must keep working, preserving the view.
  const page = await doCachedLogin(browser, {
    user: allaccessuser,
    url: 'tasks/kanban/timeline'
  });

  await expect(page).toHaveURL(/\/maintenance\/timeline\/?$/);
  await page.getByRole('tab', { name: 'Timeline', exact: true }).waitFor();
});

test('Maintenance - Timeline view renders the gantt controls', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, {
    user: allaccessuser,
    url: 'maintenance/board'
  });

  await page.getByRole('tab', { name: 'Timeline', exact: true }).click();

  // The gantt shell mounted (its zoom + navigation controls), not a placeholder.
  await page.getByText('By machine', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'gantt-today' }).waitFor();
  await expect(page.getByText('This view is coming soon.')).toHaveCount(0);

  // Back to the board.
  await page.getByRole('tab', { name: 'Board', exact: true }).click();
  await page.getByRole('button', { name: 'New work order' }).waitFor();
});

test('Maintenance - a scheduled work order appears on the timeline', async ({
  browser
}) => {
  const title = await seedScheduledCard(
    `PW Gantt ${Date.now()}`,
    `PW Gantt Machine ${Date.now()}`
  );

  const page = await doCachedLogin(browser, {
    user: allaccessuser,
    url: 'maintenance/timeline'
  });

  // The card scheduled today renders as a bar on the timeline.
  await expect(page.getByText(title).first()).toBeVisible({ timeout: 15000 });
});

test('Maintenance - Calendar view renders the calendar shell', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, {
    user: allaccessuser,
    url: 'maintenance/calendar'
  });

  // The shared Calendar shell mounted (month navigation), not the placeholder.
  await page.getByRole('button', { name: 'calendar-select-month' }).waitFor();
  await expect(page.getByText('This view is coming soon.')).toHaveCount(0);
});

test('Maintenance - a scheduled work order appears on the calendar', async ({
  browser
}) => {
  const title = await seedScheduledCard(
    `PW Scheduled ${Date.now()}`,
    `PW Cal Machine ${Date.now()}`
  );

  const page = await doCachedLogin(browser, {
    user: allaccessuser,
    url: 'maintenance/calendar'
  });

  // The event for the card scheduled today is rendered on the month grid.
  await expect(page.getByText(title)).toBeVisible({ timeout: 15000 });
});

test('Maintenance - creating a work order requires a machine', async ({
  browser
}) => {
  await seedMachine(`PW Required ${Date.now()}`);

  const page = await doCachedLogin(browser, {
    user: allaccessuser,
    url: 'maintenance/board'
  });

  await page.getByRole('button', { name: 'New work order' }).click();

  // The modal exposes a required Machine field.
  await page.getByRole('textbox', { name: 'Title' }).fill('WO without machine');
  await page.getByRole('button', { name: 'Create work order' }).click();

  // Form validation blocks the submit and names the machine field.
  await page.getByText('Select the machine for this work.').waitFor();
});

test('Maintenance - create a work order and see it on the board', async ({
  browser
}) => {
  const machineName = await seedMachine(`PW Create ${Date.now()}`);
  const cardTitle = `PW Work Order ${Date.now()}`;

  const page = await doCachedLogin(browser, {
    user: allaccessuser,
    url: 'maintenance/board'
  });

  await page.getByRole('button', { name: 'New work order' }).click();
  await page.getByRole('textbox', { name: 'Title' }).fill(cardTitle);

  // Pick the seeded machine from the required picker.
  await page.getByRole('combobox', { name: 'Machine' }).click();
  await page.getByRole('option', { name: machineName }).click();

  await page.getByRole('button', { name: 'Create work order' }).click();

  // The receipt names the created work order and offers both links; creating
  // plans the work, it does not start it.
  await page.getByText('It is planned, not started.').waitFor();
  await page.getByRole('button', { name: 'Open repair packet' }).waitFor();
  await page.getByRole('button', { name: 'Stay on the board' }).click();

  // The new card is visible on the board, showing its machine.
  await page.getByText(cardTitle).waitFor();
  await expect(page.getByText(`Machine: ${machineName}`)).toBeVisible();
});
