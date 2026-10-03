import type { Page } from '@playwright/test';

import { expect, test } from '../baseFixtures';
import { adminuser } from '../defaults';
import { navigate } from '../helpers';
import { doCachedLogin } from '../login';

/**
 * A pump's page leads back to the station it belongs to.
 *
 * The two machines are answered here rather than by the server. The demo
 * dataset registers no pump station, and what is under test is what the page
 * does with the two fields the machine endpoint returns - the endpoint itself
 * is covered by the backend's registry tests.
 */
const STATION = {
  pk: 9101,
  name: 'Harbour Road Lift Station',
  description: '',
  active: true,
  asset_type: 'pumphouse',
  parent: null,
  parent_name: null
};

const PUMP = {
  pk: 9102,
  name: 'Harbour Road Lift Station / Pump 01',
  description: '',
  active: true,
  asset_type: 'pump',
  parent: STATION.pk,
  parent_name: STATION.name
};

async function answerMachines(page: Page) {
  for (const machine of [STATION, PUMP]) {
    await page.route(
      (url) => url.pathname === `/api/assets/machines/${machine.pk}/`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(machine)
        });
      }
    );
  }
}

/** Pin the one user setting that decides whether a page names itself. */
async function showLastCrumb(page: Page, shown: boolean) {
  await page.route(
    (url) => url.pathname === '/api/settings/user/',
    async (route) => {
      const response = await route.fetch();
      const settings = await response.json();
      for (const setting of settings) {
        if (setting.key === 'ENABLE_LAST_BREADCRUMB') {
          setting.value = shown;
        }
      }
      await route.fulfill({ response, json: settings });
    }
  );
}

test('Machines - a pump leads back to its station', async ({ browser }) => {
  const page = await doCachedLogin(browser, { user: adminuser });
  await answerMachines(page);
  await showLastCrumb(page, false);

  // Arrived at by address, as from the station's own table of pumps - not by
  // clicking the tab, which is the only thing the page remembers a tab by.
  await navigate(page, `machines/machine/${PUMP.pk}/attachments`);
  await page.getByText(PUMP.name).first().waitFor();

  const trail = page.getByLabel(/^breadcrumb-\d+-/);
  await expect(trail).toHaveText(['Machines', STATION.name]);

  // The way back keeps the tab: the station's, not whichever was clicked last.
  await page.getByLabel('breadcrumb-1-harbour-road-lift-station').click();
  await page.waitForURL(`**/machines/machine/${STATION.pk}/attachments`);

  // The station stands in its own trail, as a stock location does.
  await expect(trail).toHaveText(['Machines', STATION.name]);
  await expect(page.getByText(PUMP.name)).toHaveCount(0);
});

test('Machines - a pump names itself without repeating its station', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { user: adminuser });
  await answerMachines(page);
  await showLastCrumb(page, true);

  await navigate(page, `machines/machine/${PUMP.pk}/details`);
  await page.getByText(PUMP.name).first().waitFor();

  await expect(page.getByLabel(/^breadcrumb-\d+-/)).toHaveText([
    'Machines',
    STATION.name,
    'Pump 01'
  ]);

  // A station is already the last crumb of its own trail; it is not said twice.
  await page.getByLabel('breadcrumb-1-harbour-road-lift-station').click();
  await page.waitForURL(`**/machines/machine/${STATION.pk}/**`);
  await expect(page.getByLabel(/^breadcrumb-\d+-/)).toHaveText([
    'Machines',
    STATION.name
  ]);
});
