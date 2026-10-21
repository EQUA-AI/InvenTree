import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import {
  authorityProjection,
  authorityRecord,
  captureAuthorityRegion,
  fillAuthority,
  mockAuthority,
  selectOption
} from './authorityFixtures';
import {
  expectNoPageOverflow,
  openExecutionPanel,
  panelRegion
} from './workExecutionFixtures';

nativeBaseline();
test.describe.configure({ timeout: 180_000 });
test('O15 an open Work removes its ready claim when recorded authority expires', async ({
  page
}, info) => {
  await page.clock.install({ time: new Date('2026-10-20T12:00:00Z') });
  const value = authorityProjection();
  value.physical = [
    authorityRecord('permit', {
      effective_until: '2026-10-20T12:10:00Z',
      verified_by: 'Synthetic independent verifier',
      verified_by_id: 12,
      verified_at: '2026-10-20T11:00:00Z'
    })
  ];
  const { overview } = await mockAuthority(page, value);
  const gate = overview.repair_packet!.gates[0];
  gate.status = 'verified';
  gate.is_satisfied = true;
  gate.unsatisfied_reason = '';
  await openExecutionPanel(page, 'plan');
  const panel = panelRegion(page, 'Plan & Safety');
  await expect(
    panel.getByText('No blocking gates', { exact: true })
  ).toBeVisible();
  const gateBadge = panel
    .getByRole('group', { name: 'Safety gates table, scroll for all columns' })
    .getByText('Verified', { exact: true })
    .locator('..');
  await expect(gateBadge).toHaveAttribute('style', /green/);
  await page.clock.fastForward(601_000);
  await expect(gateBadge).toHaveAttribute('style', /red/);
  await expect(
    panel.getByText('No blocking gates', { exact: true })
  ).toHaveCount(0);
  await expect(panel).toContainText(
    'Recorded authority expired. Refresh current gate checks.'
  );
  await expect(
    panel.getByRole('alert').filter({ hasText: 'This repair cannot start' })
  ).toContainText('Recorded authority expired. Refresh current gate checks.');
  await expectNoPageOverflow(page);
  await captureAuthorityRegion(
    page,
    panel
      .getByText('Safety and readiness', { exact: true })
      .locator('../../..'),
    info.outputPath('authority-readiness-expiry.png')
  );
});
for (const scheme of ['light', 'dark'] as const) {
  test(`O15 physical authority records retain source, expiry and superseded facts (${scheme})`, async ({
    page
  }, info) => {
    const value = authorityProjection();
    value.physical = [
      authorityRecord('permit', { effective_until: '2026-10-19T09:00:00Z' }),
      authorityRecord('permit', {
        id: 800,
        current: false,
        statement: 'Synthetic superseded permit'
      })
    ];
    await mockAuthority(page, value);
    await openExecutionPanel(page, 'plan', scheme);
    const panel = panelRegion(page, 'Plan & Safety');
    await expect(panel).toContainText('Synthetic Recorder');
    await expect(panel).toContainText('Synthetic site document / revision 4');
    await expect(panel).toContainText('Expired');
    await expect(panel).toContainText(
      'recorded authority outside its effective window'
    );
    await expect(
      panel.getByRole('alert').filter({ hasText: 'This repair cannot start' })
    ).toContainText('recorded authority outside its effective window');
    await expect(
      panel.getByText('No blocking gates', { exact: true })
    ).toHaveCount(0);
    await expect(panel.getByTestId('authority-recorder')).toHaveCount(0);
    const packetCard = panel.getByRole('heading', {
      name: 'Repair packet',
      exact: true
    });
    await expect(packetCard).toBeVisible();
    const safetyTable = panel.getByRole('group', {
      name: 'Safety gates table, scroll for all columns'
    });
    const status = safetyTable.getByText('Confirmed', { exact: true });
    await expect(status.locator('..')).toHaveAttribute('style', /red/);
    const width = await status.evaluate((element) => ({
      content: element.scrollWidth,
      visible: element.clientWidth
    }));
    expect(
      width.content,
      'Native gate status must remain fully readable'
    ).toBeLessThanOrEqual(width.visible + 1);
    await captureAuthorityRegion(
      page,
      safetyTable,
      info.outputPath(`authority-safety-gates-${scheme}.png`)
    );
    await expectNoPageOverflow(page);
    await panel
      .getByText('Recorded physical authority', { exact: true })
      .scrollIntoViewIfNeeded();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: info.outputPath(`authority-plan-${scheme}.png`),
      fullPage: true
    });
    await captureAuthorityRegion(
      page,
      panel
        .getByRole('heading', {
          name: 'Recorded physical authority',
          exact: true
        })
        .locator('..'),
      info.outputPath(`authority-plan-section-${scheme}.png`)
    );
    await panel
      .getByRole('button', { name: /Superseded authority records/ })
      .click();
    await expect(panel).toContainText('Synthetic superseded permit');
  });

  test(`O15 Verify separates accepted test, withheld permission and observed stopped (${scheme})`, async ({
    page
  }, info) => {
    const value = authorityProjection();
    value.operational = [
      'test_acceptance',
      'operating_permission',
      'observed_return'
    ].map((kind, i) =>
      authorityRecord(kind as 'test_acceptance', { id: 900 + i })
    );
    await mockAuthority(page, value);
    await openExecutionPanel(page, 'verify', scheme);
    const panel = panelRegion(page, 'Verify & Return');
    await expect(panel).toContainText('Test acceptance · accepted');
    await expect(panel).toContainText('Asset / station: Synthetic pump');
    await expect(panel).not.toContainText(
      'Asset / station: Synthetic existing isolation / permit gate'
    );
    await expect(panel).toContainText('Operating permission · withheld');
    await expect(panel).toContainText(
      'Observed operating return · observed stopped'
    );
    await expect(panel.getByTestId('authority-recorder')).toHaveCount(0);
    const packetCard = panel.getByRole('heading', {
      name: 'Repair packet',
      exact: true
    });
    if (await packetCard.count()) {
      await expect(
        packetCard
          .locator('../../..')
          .getByText('Confirmed', { exact: true })
          .locator('..')
      ).toHaveAttribute('style', /red/);
      await captureAuthorityRegion(
        page,
        packetCard.locator('../../..'),
        info.outputPath(`authority-packet-card-${scheme}.png`)
      );
    }
    await expectNoPageOverflow(page);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: info.outputPath(`authority-verify-${scheme}.png`),
      fullPage: true
    });
    await captureAuthorityRegion(
      page,
      panel
        .getByRole('heading', { name: 'Physical verification', exact: true })
        .locator('..'),
      info.outputPath(`authority-acceptance-section-${scheme}.png`)
    );
    await captureAuthorityRegion(
      page,
      panel
        .getByRole('heading', {
          name: 'Release and operating return',
          exact: true
        })
        .locator('..'),
      info.outputPath(`authority-return-section-${scheme}.png`)
    );
  });
}

test('O15 authorized permit recording uses the exact gate and version once', async ({
  page
}, info) => {
  const { commands } = await mockAuthority(page, authorityProjection(true));
  await openExecutionPanel(page, 'plan');
  await page
    .getByRole('button', {
      name: 'Record isolation, permit or access window',
      exact: true
    })
    .click();
  await fillAuthority(page);
  await selectOption(
    page,
    'Existing safety gate',
    'Synthetic existing isolation / permit gate'
  );
  await page
    .getByRole('button', { name: 'Save attributed record', exact: true })
    .click();
  await expect(
    panelRegion(page, 'Plan & Safety').getByText('Authority record saved', {
      exact: false
    })
  ).toBeVisible();
  expect(commands).toHaveLength(1);
  await expect(
    page.getByRole('textbox', {
      name: 'Recorded fact and conditions',
      exact: true
    })
  ).toHaveValue('');
  await expect(
    page.getByRole('button', { name: 'Save attributed record', exact: true })
  ).toBeDisabled();
  expect(commands[0]).toMatchObject({
    expected_version: 7,
    expected_context: 'a'.repeat(64),
    operation: 'record',
    fields: {
      gate_id: 501,
      kind: 'permit',
      source_reference: 'Synthetic document / P-204 / revision 2'
    }
  });
  await expectNoPageOverflow(page);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath('authority-plan-recorded.png'),
    fullPage: true
  });
  await captureAuthorityRegion(
    page,
    panelRegion(page, 'Plan & Safety').getByTestId('authority-recorder'),
    info.outputPath('authority-recorded-form-light.png')
  );
});

test('O15 uncertain save survives reload and reconciles the original intent', async ({
  page
}, info) => {
  test.setTimeout(180_000);
  const server = await mockAuthority(page, authorityProjection(true));
  server.interrupt();
  await openExecutionPanel(page, 'plan', 'dark');
  await page
    .getByRole('button', {
      name: 'Record isolation, permit or access window',
      exact: true
    })
    .click();
  await fillAuthority(page);
  await selectOption(
    page,
    'Existing safety gate',
    'Synthetic existing isolation / permit gate'
  );
  await captureAuthorityRegion(
    page,
    panelRegion(page, 'Plan & Safety').getByTestId('authority-recorder'),
    info.outputPath('authority-ready-form-dark.png')
  );
  await page
    .getByRole('button', { name: 'Save attributed record', exact: true })
    .click();
  await expect(
    page.getByRole('button', {
      name: 'Check original authority operation',
      exact: true
    })
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole('button', {
      name: 'Check original authority operation',
      exact: true
    })
  ).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath('authority-uncertain-dark.png'),
    fullPage: true
  });
  await captureAuthorityRegion(
    page,
    panelRegion(page, 'Plan & Safety').getByTestId('authority-recorder'),
    info.outputPath('authority-recovery-section-dark.png')
  );
  await page
    .getByRole('button', {
      name: 'Check original authority operation',
      exact: true
    })
    .click();
  await expect(
    panelRegion(page, 'Plan & Safety').getByText('Authority record saved', {
      exact: false
    })
  ).toBeVisible();
  expect(server.commands).toHaveLength(2);
  expect(server.commands[1]).toEqual(server.commands[0]);
  expect(server.value.physical).toHaveLength(1);
});

test('O15 changed source context retains the draft until an explicit fresh review', async ({
  page
}, info) => {
  const server = await mockAuthority(page, authorityProjection(true));
  await openExecutionPanel(page, 'plan');
  await page
    .getByRole('button', {
      name: 'Record isolation, permit or access window',
      exact: true
    })
    .click();
  await fillAuthority(page);
  await selectOption(
    page,
    'Existing safety gate',
    'Synthetic existing isolation / permit gate'
  );
  // Scope/retest writers need not advance the Work lifecycle version.
  server.value.context_token = 'b'.repeat(64);
  await page
    .getByRole('button', { name: 'Save attributed record', exact: true })
    .click();
  await expect(
    page.getByRole('button', {
      name: 'Review latest Work after refusal',
      exact: true
    })
  ).toBeVisible();
  await expect(
    page.getByRole('textbox', {
      name: 'Recorded fact and conditions',
      exact: true
    })
  ).toHaveValue('Synthetic permit P-204, restricted scope');
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath('authority-stale-draft.png'),
    fullPage: true
  });
  await captureAuthorityRegion(
    page,
    panelRegion(page, 'Plan & Safety').getByTestId('authority-recorder'),
    info.outputPath('authority-stale-form-light.png')
  );
  await page
    .getByRole('button', {
      name: 'Review latest Work after refusal',
      exact: true
    })
    .click();
  await expect(
    page.getByRole('button', { name: 'Save attributed record', exact: true })
  ).toBeEnabled();
  await page
    .getByRole('button', { name: 'Save attributed record', exact: true })
    .click();
  await expect(
    panelRegion(page, 'Plan & Safety').getByText('Authority record saved', {
      exact: false
    })
  ).toBeVisible();
  expect(server.commands.map((command) => command.expected_context)).toEqual([
    'a'.repeat(64),
    'b'.repeat(64)
  ]);
  expect(server.commands.map((command) => command.expected_version)).toEqual([
    7, 7
  ]);
  expect(server.value.physical).toHaveLength(1);
});

test('O15 second person verification appends its separate attribution', async ({
  page
}, info) => {
  const value = authorityProjection(true);
  value.physical = [
    authorityRecord('permit', { effective_state: 'awaiting_verification' })
  ];
  const { commands } = await mockAuthority(page, value);
  await openExecutionPanel(page, 'plan');
  await page.getByRole('button', { name: /Verify independently/ }).click();
  await expect(
    page.getByText('Synthetic Second Person', { exact: false })
  ).toBeVisible();
  expect(commands[0]).toMatchObject({
    operation: 'verify',
    supersedes: 801,
    fields: { gate_id: 501 }
  });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath('authority-second-person.png'),
    fullPage: true
  });
});

test('O15 maintenance acceptance and operations return use separate grants and records', async ({
  page
}, info) => {
  test.setTimeout(180_000);
  const value = authorityProjection();
  value.commands.test_acceptance = true;
  const server = await mockAuthority(page, value);
  await openExecutionPanel(page, 'verify');
  const panel = panelRegion(page, 'Verify & Return');
  let recorder = panel.getByTestId('authority-recorder').first();
  await page
    .getByRole('button', {
      name: 'Record test acceptance or operating fact',
      exact: true
    })
    .click();
  await page
    .getByRole('combobox', { name: 'Record kind', exact: true })
    .click();
  await expect(
    page.getByRole('option', { name: 'Test acceptance', exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('option', { name: 'Operating permission', exact: true })
  ).toHaveCount(0);
  await page
    .getByRole('option', { name: 'Test acceptance', exact: true })
    .click();
  await selectOption(page, 'Recorded outcome', 'accepted');
  await recorder
    .getByRole('textbox', { name: 'Recorded fact and conditions', exact: true })
    .fill('Synthetic maintenance acceptance for test sheet T-31');
  await recorder
    .getByRole('textbox', {
      name: 'Source document / record reference',
      exact: true
    })
    .fill('Synthetic test sheet T-31 / revision 2');
  await captureAuthorityRegion(
    page,
    recorder,
    info.outputPath('authority-test-acceptance-form-light.png')
  );
  await recorder
    .getByRole('button', { name: 'Save attributed record', exact: true })
    .click();
  await expect(
    panel.getByText('Test acceptance · accepted', { exact: true }).first()
  ).toBeVisible();
  expect(server.value.operational.map((row) => row.kind)).toEqual([
    'test_acceptance'
  ]);

  // A separately granted operations capability does not create either fact.
  value.commands.test_acceptance = false;
  value.commands.operating_permission = true;
  value.commands.observed_return = true;
  value.commands.service_state = true;
  await page.reload();
  recorder = panel.getByTestId('authority-recorder').first();
  await recorder
    .getByRole('button', {
      name: 'Record test acceptance or operating fact',
      exact: true
    })
    .click();
  await recorder
    .getByRole('combobox', { name: 'Record kind', exact: true })
    .click();
  await expect(
    page.getByRole('option', { name: 'Test acceptance', exact: true })
  ).toHaveCount(0);
  await page
    .getByRole('option', { name: 'Operating permission', exact: true })
    .click();
  await selectOption(page, 'Recorded outcome', 'granted');
  await recorder
    .getByRole('textbox', { name: 'Recorded fact and conditions', exact: true })
    .fill('Synthetic operating permission for the recorded test scope');
  await recorder
    .getByRole('textbox', {
      name: 'Source document / record reference',
      exact: true
    })
    .fill('Synthetic operating log O-17');
  await recorder
    .getByRole('textbox', { name: 'Effective until / expiry', exact: true })
    .fill('2099-10-20T18:00');
  await captureAuthorityRegion(
    page,
    recorder,
    info.outputPath('authority-operating-permission-form-light.png')
  );
  await recorder
    .getByRole('button', { name: 'Save attributed record', exact: true })
    .click();
  await expect(
    panel.getByText('Operating permission · granted', { exact: true }).first()
  ).toBeVisible();
  expect(server.value.operational.map((row) => row.kind)).toEqual([
    'operating_permission',
    'test_acceptance'
  ]);

  await selectOption(page, 'Record kind', 'Observed operating return');
  await selectOption(page, 'Recorded outcome', 'observed running');
  await recorder
    .getByRole('textbox', { name: 'Recorded fact and conditions', exact: true })
    .fill('Synthetic operator observed the pump running');
  await recorder
    .getByRole('textbox', {
      name: 'Source document / record reference',
      exact: true
    })
    .fill('Synthetic operating log O-18');
  await captureAuthorityRegion(
    page,
    recorder,
    info.outputPath('authority-observed-return-form-light.png')
  );
  await recorder
    .getByRole('button', { name: 'Save attributed record', exact: true })
    .click();
  await expect(
    panel
      .getByText('Observed operating return · observed running', {
        exact: true
      })
      .first()
  ).toBeVisible();
  expect(
    server.commands.map((command) => [
      command.fields.kind,
      command.expected_version
    ])
  ).toEqual([
    ['test_acceptance', 7],
    ['operating_permission', 8],
    ['observed_return', 9]
  ]);
  expect(
    server.value.operational.every(
      (row) => row.recorded_by_id === 11 && row.target_id === 18
    )
  ).toBe(true);
  expect(
    server.commands.every((command) => !('gate_id' in command.fields))
  ).toBe(true);
  await expectNoPageOverflow(page);
});

test('O15 an uncertain physical operation locks sibling recording forms until explicit reconciliation', async ({
  page
}) => {
  const server = await mockAuthority(page, authorityProjection(true));
  server.interrupt();
  await openExecutionPanel(page, 'plan');
  await page
    .getByRole('button', {
      name: 'Record isolation, permit or access window',
      exact: true
    })
    .click();
  await fillAuthority(page);
  await selectOption(
    page,
    'Existing safety gate',
    'Synthetic existing isolation / permit gate'
  );
  await page
    .getByRole('button', { name: 'Save attributed record', exact: true })
    .click();
  await expect(
    page.getByRole('button', {
      name: 'Check original authority operation',
      exact: true
    })
  ).toBeVisible();
  await page.getByRole('tab', { name: 'Verify & Return', exact: true }).click();
  const panel = panelRegion(page, 'Verify & Return');
  const recorder = panel.getByTestId('authority-recorder').first();
  await expect(recorder).toContainText(
    'Synthetic permit P-204, restricted scope'
  );
  await recorder
    .getByRole('button', {
      name: 'Record test acceptance or operating fact',
      exact: true
    })
    .click();
  await expect(
    recorder.getByRole('button', {
      name: 'Save attributed record',
      exact: true
    })
  ).toBeDisabled();
  expect(server.commands).toHaveLength(1);
  await recorder
    .getByRole('button', {
      name: 'Check original authority operation',
      exact: true
    })
    .click();
  await expect(recorder).toContainText('Authority record saved');
  expect(server.commands).toHaveLength(2);
  expect(server.commands[1]).toEqual(server.commands[0]);
  expect(server.value.operational).toHaveLength(0);
});

test('O15 permit after acceptance repins mounted drafts and resets first-edit clocks', async ({
  page
}, info) => {
  test.setTimeout(300_000);
  await page.clock.install({ time: new Date('2026-10-20T12:00:00Z') });
  const server = await mockAuthority(page, authorityProjection(true));
  await openExecutionPanel(page, 'plan');
  await page.clock.fastForward(600_000);
  await page
    .getByRole('button', {
      name: 'Record isolation, permit or access window',
      exact: true
    })
    .click();
  await fillAuthority(page);
  await selectOption(
    page,
    'Existing safety gate',
    'Synthetic existing isolation / permit gate'
  );
  await page.getByRole('tab', { name: 'Verify & Return', exact: true }).click();
  const verify = panelRegion(page, 'Verify & Return')
    .getByTestId('authority-recorder')
    .first();
  await verify
    .getByRole('button', {
      name: 'Record test acceptance or operating fact',
      exact: true
    })
    .click();
  await selectOption(page, 'Record kind', 'Test acceptance');
  await selectOption(page, 'Recorded outcome', 'accepted');
  await verify
    .getByRole('textbox', { name: 'Recorded fact and conditions', exact: true })
    .fill('Synthetic maintenance test accepted');
  await verify
    .getByRole('textbox', {
      name: 'Source document / record reference',
      exact: true
    })
    .fill('Synthetic test sheet');
  await verify
    .getByRole('button', { name: 'Save attributed record', exact: true })
    .click();
  await expect(verify).toContainText('Version 8');
  await page.getByRole('tab', { name: 'Plan & Safety', exact: true }).click();
  const plan = panelRegion(page, 'Plan & Safety');
  await expect(
    plan.getByRole('textbox', {
      name: 'Recorded fact and conditions',
      exact: true
    })
  ).toHaveValue('Synthetic permit P-204, restricted scope');
  await plan
    .getByRole('button', { name: 'Save attributed record', exact: true })
    .click();
  await expect(plan).toContainText('Version 9');
  expect(server.commands).toHaveLength(2);
  expect(server.commands[1]).toMatchObject({
    expected_version: 8,
    fields: {
      kind: 'permit',
      observed_at: '2026-10-20T12:10:00.000Z',
      effective_from: '2026-10-20T12:10:00.000Z'
    }
  });
  await page.clock.fastForward(600_000);
  await fillAuthority(page);
  await plan
    .getByRole('button', { name: 'Save attributed record', exact: true })
    .click();
  await expect(plan).toContainText('Version 10');
  expect(server.commands[2].fields.observed_at).toBe(
    '2026-10-20T12:20:00.000Z'
  );
  expect(server.commands[2].fields.effective_from).toBe(
    '2026-10-20T12:20:00.000Z'
  );
  await captureAuthorityRegion(
    page,
    plan.getByTestId('authority-recorder'),
    info.outputPath('authority-permit-after-acceptance.png')
  );
  await expectNoPageOverflow(page);
});

test('O15 stale overview after acceptance replaces old success with a visible draft refusal', async ({
  page
}, info) => {
  const server = await mockAuthority(page, authorityProjection(true));
  await openExecutionPanel(page, 'verify');
  const verify = panelRegion(page, 'Verify & Return')
    .getByTestId('authority-recorder')
    .first();
  await verify
    .getByRole('button', {
      name: 'Record test acceptance or operating fact',
      exact: true
    })
    .click();
  await selectOption(page, 'Record kind', 'Test acceptance');
  await selectOption(page, 'Recorded outcome', 'accepted');
  await verify
    .getByRole('textbox', { name: 'Recorded fact and conditions', exact: true })
    .fill('Synthetic accepted test');
  await verify
    .getByRole('textbox', {
      name: 'Source document / record reference',
      exact: true
    })
    .fill('Synthetic source');
  await verify
    .getByRole('button', { name: 'Save attributed record', exact: true })
    .click();
  await expect(verify).toContainText('Version 8');
  // A lagging authorized overview must not turn an older new draft into a no-op.
  server.value.work_version = 7;
  server.overview.lifecycle_version = 7;
  await openExecutionPanel(page, 'plan');
  await page
    .getByRole('button', {
      name: 'Record isolation, permit or access window',
      exact: true
    })
    .click();
  await fillAuthority(page);
  await selectOption(
    page,
    'Existing safety gate',
    'Synthetic existing isolation / permit gate'
  );
  const plan = panelRegion(page, 'Plan & Safety');
  await plan
    .getByRole('button', { name: 'Save attributed record', exact: true })
    .click();
  await expect(plan).toContainText(
    'This Work changed. Review the latest saved Work'
  );
  await expect(
    plan.getByText('Authority record saved', { exact: false })
  ).toHaveCount(0);
  await expect(
    plan.getByRole('button', {
      name: 'Review latest Work after refusal',
      exact: true
    })
  ).toBeVisible();
  await expect(
    plan.getByRole('textbox', {
      name: 'Recorded fact and conditions',
      exact: true
    })
  ).toHaveValue('Synthetic permit P-204, restricted scope');
  expect(server.commands).toHaveLength(1);
  await captureAuthorityRegion(
    page,
    plan.getByTestId('authority-recorder'),
    info.outputPath('authority-stale-after-acceptance.png')
  );
});

for (const scheme of ['light', 'dark'] as const) {
  test(`O15 native roles expose an independent operations grant (${scheme})`, async ({
    page
  }, info) => {
    await page.addInitScript(
      (scheme) => localStorage.setItem('mantine-color-scheme-value', scheme),
      scheme
    );
    const writes: Record<string, unknown>[] = [];
    await page.route('**/api/user/ruleset/24/', async (route) => {
      expect(route.request().method()).toBe('PATCH');
      const body = route.request().postDataJSON();
      writes.push(body);
      return route.fulfill({ json: { pk: 24, ...body } });
    });
    await page.goto('/playwright/approval-role.html');
    await expect(page.locator('html')).toHaveAttribute(
      'data-mantine-color-scheme',
      scheme
    );
    const row = page.getByTestId('lifecycle-permissions-row');
    const operating = row.getByRole('checkbox', {
      name: 'Can record operating authority and service state',
      exact: true
    });
    const maintenance = row.getByRole('checkbox', {
      name: 'Can execute work orders',
      exact: true
    });
    await expect(operating).not.toBeChecked();
    await expect(maintenance).not.toBeChecked();
    await operating.check();
    await expect(maintenance).not.toBeChecked();
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await expect.poll(() => writes.length).toBe(1);
    expect(writes[0].can_record_operating_authority).toBe(true);
    expect(writes[0].can_execute_workorder).not.toBe(true);
    await captureAuthorityRegion(
      page,
      row,
      info.outputPath(`authority-native-role-${scheme}.png`)
    );
  });
}
