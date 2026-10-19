import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import {
  closeoutWork,
  containment,
  openCloseout,
  story,
  values
} from './closeoutFixtures';

nativeBaseline();
for (const scheme of ['light', 'dark'] as const) {
  test(`prepared report is direct, sourced and read-only (${scheme})`, async ({
    page
  }, info) => {
    const { commands, reads } = await closeoutWork(page);
    await openCloseout(page, scheme);
    await expect(
      page.getByRole('heading', {
        name: 'Prepared maintenance account',
        exact: true
      })
    ).toBeVisible();
    await expect(page.getByTestId('prepared-closeout')).toContainText(
      values.action
    );
    await expect(
      page.getByRole('button', {
        name: 'Update maintenance record',
        exact: true
      })
    ).toBeEnabled();
    await expect(page.getByTestId('prepared-closeout')).toContainText(
      'Not established'
    );
    await expect(page.getByTestId('prepared-closeout')).toContainText(
      'Initial test: 6 mm/s · failed'
    );
    await expect(page.getByTestId('prepared-closeout')).toContainText(
      'Repeat test: 0 mm/s · verified'
    );
    await expect(
      page.getByRole('button', { name: 'Add reading', exact: true })
    ).toHaveCount(0);
    await page.getByText('Retained story revisions', { exact: true }).click();
    await expect(
      page.getByText('Original transcript: adjusted seal.', { exact: true })
    ).toBeVisible();
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Download account (.txt)' }).click();
    expect((await download).suggestedFilename()).toContain('work-73-account');
    expect(commands).toEqual([]);
    expect(reads.filter((p) => p.endsWith('/readings/'))).toHaveLength(1);
    await containment(page);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: info.outputPath(`prepared-report-${scheme}.png`),
      fullPage: true
    });
  });
}
test('missing story does not prefill the mutually exclusive typed fallback or start a capture', async ({
  page
}, info) => {
  const { commands } = await closeoutWork(page, { captures: [] });
  await openCloseout(page);
  await expect(
    page.getByRole('textbox', { name: 'What did you do?', exact: true })
  ).toHaveValue('');
  await expect(page.getByTestId('dictate-closeout')).toBeEnabled();
  await expect(page.getByText(/No saved technician story/)).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Update maintenance record', exact: true })
  ).toBeDisabled();
  expect(commands).toEqual([]);
  await page.screenshot({
    path: info.outputPath('missing-story.png'),
    fullPage: true
  });
});
test('active capture locks final update and preserves its original narrative', async ({
  page
}) => {
  const { commands } = await closeoutWork(page, {
    captures: [{ ...story, status: 'extracting' }],
    ready: false
  });
  await openCloseout(page);
  await expect(
    page.getByRole('button', { name: 'Update maintenance record', exact: true })
  ).toBeDisabled();
  await expect(page.getByTestId('work-record-update')).toContainText(
    'Finish the active capture review'
  );
  await expect(page.getByTestId('dictate-closeout')).toHaveCount(0);
  expect(commands).toEqual([]);
});
test('unchanged extraction is accepted and a technician correction is edited', async ({
  page
}) => {
  const { commands } = await closeoutWork(page);
  await page.route('**/closeout/captures/12/decisions/', async (route) => {
    commands.push({
      path: 'decisions',
      body: route.request().postDataJSON(),
      method: 'POST'
    });
    await route.fulfill({ json: {} });
  });
  await openCloseout(page);
  await page.getByText('Review', { exact: true }).click();
  await page
    .getByRole('textbox', { name: 'Result', exact: true })
    .fill('Corrected observation');
  await page
    .getByRole('button', { name: 'Record decisions', exact: true })
    .click();
  await expect.poll(() => commands.length).toBe(1);
  expect(commands[0].body.decisions).toContainEqual({
    field_path: 'action',
    decision: 'accepted'
  });
  expect(commands[0].body.decisions).toContainEqual({
    field_path: 'result',
    decision: 'edited',
    final_value: 'Corrected observation'
  });
});
test('accepted report retains original snapshot and attributed amendment; History remains chronological', async ({
  page
}, info) => {
  const { commands, setOverview } = await closeoutWork(page, {
    completed: true,
    captures: [{ ...story, status: 'consumed' }]
  });
  setOverview({
    events: [
      {
        id: 2,
        event_type: 'AMENDED',
        actor: 4,
        from_status: 'completed',
        to_status: 'completed',
        reason: 'Corrected observation',
        created_at: '2026-10-19T10:05:00Z'
      },
      {
        id: 1,
        event_type: 'COMPLETED',
        actor: 3,
        from_status: 'verifying',
        to_status: 'completed',
        reason: 'Original accepted record',
        created_at: '2026-10-19T09:00:00Z'
      }
    ]
  });
  await openCloseout(page, 'dark');
  await page.getByText('Original accepted snapshot', { exact: true }).click();
  await expect(page.getByTestId('prepared-closeout')).toContainText(
    values.result
  );
  await expect(page.getByTestId('prepared-closeout')).toContainText(
    'Corrected the recorded observation'
  );
  await expect(page.getByTestId('prepared-closeout')).toContainText(
    'Requested by user 3'
  );
  await expect(
    page.getByRole('button', { name: 'Update maintenance record', exact: true })
  ).toHaveCount(0);
  await containment(page);
  await page.screenshot({
    path: info.outputPath('accepted-report-dark.png'),
    fullPage: true
  });
  await page.getByRole('tab', { name: 'History', exact: true }).click();
  const history = page.getByRole('tabpanel', { name: 'History', exact: true });
  expect(await history.innerText()).toMatch(
    /Original accepted record[\s\S]*Corrected observation/
  );
  expect(commands).toEqual([]);
});
test('source failure never looks like an empty or ready account', async ({
  page
}) => {
  await closeoutWork(page, { sourceError: true });
  await openCloseout(page);
  await expect(
    page.getByText(/Some closeout sources could not be read/)
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Download account (.txt)' })
  ).toBeDisabled();
  await expect(
    page.getByRole('button', { name: 'Update maintenance record', exact: true })
  ).toBeDisabled();
});
test('Verify owns native reading entry and shares the retained query with Closeout', async ({
  page
}) => {
  const { reads, commands } = await closeoutWork(page);
  await openCloseout(page);
  await page.getByRole('tab', { name: 'Verify & Return', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Add reading', exact: true })
  ).toBeVisible();
  await page.getByRole('tab', { name: 'Closeout', exact: true }).click();
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
  // The same key is shared. A second observer may revalidate stale data once;
  // returning to the kept-mounted Closeout must not create another reader.
  expect(
    reads.filter((p) => p.endsWith('/readings/')).length
  ).toBeLessThanOrEqual(2);
  expect(commands).toEqual([]);
});
