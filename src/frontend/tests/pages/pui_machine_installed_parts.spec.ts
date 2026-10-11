import type { Page } from '@playwright/test';

import { expect, test } from '../baseFixtures';
import { adminuser } from '../defaults';
import { navigate } from '../helpers';
import { doCachedLogin } from '../login';

/**
 * The Installed Parts tab of a machine page.
 *
 * A registered pump or station shows the components the equipment registry
 * holds for it; plain equipment shows the parts recorded as fitted, as before.
 *
 * Everything is answered here rather than by the server. The demo dataset
 * registers no pump station, and what is under test is what the tab draws from
 * the registry's answer - the answer itself is covered by the backend's
 * registry tests.
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

const PRESS = {
  pk: 9103,
  name: 'Hydraulic Press',
  description: '',
  active: true,
  asset_type: 'equipment',
  parent: null,
  parent_name: null
};

const INFERRED =
  'Inferred from source tags; physical assignment is unverified.';

function component(pk: number, owner: typeof STATION | typeof PUMP, rest: any) {
  return {
    pk,
    uuid: `00000000-0000-4000-8000-${String(pk).padStart(12, '0')}`,
    machine: owner.pk,
    machine_name: owner.name,
    virtual: false,
    status: 'draft',
    provenance: INFERRED,
    review_note: '',
    reviewed_at: null,
    ...rest
  };
}

const MOTOR = component(1, PUMP, {
  part: 501,
  part_name: 'Electric Motor',
  code: 'PS-MOTOR:1',
  name: 'Electric Motor'
});

const ELECTRICAL = component(2, PUMP, {
  part: 502,
  part_name: 'Motor Electrical System',
  code: 'PS-ELECTRICAL:1',
  name: 'Motor Electrical System',
  virtual: true,
  status: 'verified',
  provenance: 'Explicit dictionary review crosswalk'
});

const FOREBAY = component(3, STATION, {
  part: 503,
  part_name: 'Forebay and Intake Structure',
  code: 'PS-FOREBAY:1',
  name: 'Forebay and Intake Structure'
});

const SPARE = {
  pk: 71,
  part: 504,
  part_name: 'Mechanical seal kit',
  part_group: 'Seals',
  quantity: 2,
  notes: ''
};

function json(body: unknown) {
  return {
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body)
  };
}

/** Answer for the machines, the registry and the recorded parts. */
async function answer(page: Page, recorded: Record<number, object[]>) {
  for (const machine of [STATION, PUMP, PRESS]) {
    await page.route(
      (url) => url.pathname === `/api/assets/machines/${machine.pk}/`,
      (route) => route.fulfill(json(machine))
    );
  }

  const held: Record<number, object[]> = {
    [PUMP.pk]: [MOTOR, ELECTRICAL],
    [STATION.pk]: [FOREBAY, MOTOR, ELECTRICAL]
  };
  for (const [owner, rows] of Object.entries(held)) {
    await page.route(
      (url) => url.pathname === `/api/assets/registry/${owner}/components/`,
      (route) => route.fulfill(json({ count: rows.length, results: rows }))
    );
  }

  await page.route(
    (url) => url.pathname === '/api/assets/parts/',
    (route) => {
      const machine = Number(
        new URL(route.request().url()).searchParams.get('machine')
      );
      const rows = (recorded[machine] ?? []).map((row) => ({
        ...row,
        machine
      }));
      return route.fulfill(json({ count: rows.length, results: rows }));
    }
  );
}

test('Machines - a pump lists the components the registry holds for it', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { user: adminuser });
  await answer(page, {});

  await navigate(page, `machines/machine/${PUMP.pk}/parts`);
  await page
    .getByText('The components the equipment registry holds for this pump.')
    .waitFor();

  const motor = page.getByRole('row').filter({ hasText: 'PS-MOTOR:1' });
  await expect(motor).toContainText('Electric Motor');
  await expect(motor).toContainText('Draft');
  await expect(motor).toContainText(INFERRED);
  await expect(motor).not.toContainText('Logical group');
  await expect(
    motor.getByRole('link', { name: 'Electric Motor' })
  ).toHaveAttribute('href', /\/part\/501\/$/);

  const electrical = page
    .getByRole('row')
    .filter({ hasText: 'PS-ELECTRICAL:1' });
  await expect(electrical).toContainText('Logical group');
  await expect(electrical).toContainText('Verified');

  // On a pump every row is the pump's own: no column says so.
  await expect(
    page.getByRole('columnheader', { name: 'Equipment' })
  ).toHaveCount(0);

  await expect(
    page.getByRole('link', { name: 'Review in the equipment registry' })
  ).toHaveAttribute(
    'href',
    new RegExp(`/machines/registry/\\?station=${STATION.pk}&owner=${PUMP.pk}$`)
  );

  // Nothing is recorded as fitted, so no second, empty table is drawn.
  await expect(page.getByText('Recorded as fitted')).toHaveCount(0);
});

test('Machines - a station lists its own components and its pumps', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { user: adminuser });
  await answer(page, {});

  await navigate(page, `machines/machine/${STATION.pk}/parts`);
  await page
    .getByText(
      'The components the equipment registry holds for this station and its pumps.'
    )
    .waitFor();

  await expect(
    page.getByRole('columnheader', { name: 'Equipment' })
  ).toBeVisible();

  const forebay = page.getByRole('row').filter({ hasText: 'PS-FOREBAY:1' });
  await expect(forebay).toContainText('Station');

  // A pump's rows name the pump, without the station, and lead to its page.
  const motor = page.getByRole('row').filter({ hasText: 'PS-MOTOR:1' });
  await expect(
    motor.getByRole('link', { name: 'Pump 01', exact: true })
  ).toHaveAttribute('href', new RegExp(`/machines/machine/${PUMP.pk}/parts$`));

  await expect(
    page.getByRole('link', { name: 'Review in the equipment registry' })
  ).toHaveAttribute(
    'href',
    new RegExp(`/machines/registry/\\?station=${STATION.pk}$`)
  );
});

test('Machines - a part recorded against a pump is not hidden by its components', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { user: adminuser });
  await answer(page, { [PUMP.pk]: [SPARE] });

  await navigate(page, `machines/machine/${PUMP.pk}/parts`);
  await page.getByText('PS-MOTOR:1').waitFor();

  await page.getByText('Recorded as fitted').waitFor();
  await expect(
    page.getByRole('row').filter({ hasText: 'Mechanical seal kit' })
  ).toContainText('Seals');
});

test('Machines - plain equipment still lists the parts recorded as fitted', async ({
  browser
}) => {
  const page = await doCachedLogin(browser, { user: adminuser });
  await answer(page, { [PRESS.pk]: [SPARE] });

  await navigate(page, `machines/machine/${PRESS.pk}/parts`);
  const seal = page.getByRole('row').filter({ hasText: 'Mechanical seal kit' });
  await seal.waitFor();
  await expect(seal).toContainText('Seals');

  // No registry, so nothing about it: the tab is the table it always was.
  await expect(page.getByText(/equipment registry/)).toHaveCount(0);
  await expect(page.getByText('Recorded as fitted')).toHaveCount(0);
  await expect(page.getByText('PS-MOTOR:1')).toHaveCount(0);
});
