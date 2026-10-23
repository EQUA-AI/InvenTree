import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import {
  authorityProjection,
  authorityRecord,
  captureAuthorityRegion,
  mockAuthority,
  selectOption
} from '../s4/authorityFixtures';
import {
  expectNoPageOverflow,
  openExecutionPanel,
  panelRegion
} from '../s4/workExecutionFixtures';

nativeBaseline();
test.describe.configure({ timeout: 180_000 });
for (const scheme of ['light', 'dark'] as const) {
  test(`O15 operator service facts retain unknown capacity and telemetry source clock (${scheme})`, async ({
    page
  }, info) => {
    const value = authorityProjection();
    value.telemetry = [
      {
        binding_id: 601,
        reading_token: 'c'.repeat(64),
        target_id: 18,
        target_name: 'Synthetic pump',
        tag: 'MOTOR_ON_STATUS',
        path: '/motor/run',
        signal_kind: 'run_context',
        source: 'Synthetic read-only SCADA',
        source_id: 41,
        source_sequence: 10,
        source_active: true,
        value: { value: 0 },
        observed_at: '2026-10-19T06:30:00Z',
        quality: 'uncertain',
        stale: true
      }
    ];
    value.operational = [
      authorityRecord('service_state', {
        details: {
          duty: 'Synthetic assigned duty pending operations confirmation',
          standby: 'Synthetic standby unavailable',
          redundancy: null,
          restrictions: 'Synthetic temporary load restriction'
        },
        basis: { telemetry: value.telemetry[0] }
      })
    ];
    await mockAuthority(page, value);
    await openExecutionPanel(page, 'verify', scheme);
    const brief = panelRegion(page, 'Verify & Return').getByTestId(
      'service-restoration'
    );
    await expect(brief).toContainText('Synthetic standby unavailable');
    await expect(brief).toContainText('redundancy: Not recorded');
    await expect(brief).toContainText('Synthetic Recorder');
    await expect(brief).toContainText('Read-only operating telemetry context');
    await expect(brief).toContainText('Stale');
    await expect(brief).toContainText(
      'Telemetry basis: MOTOR_ON_STATUS · 0 · Synthetic read-only SCADA'
    );
    await expect(brief.getByTestId('authority-recorder')).toHaveCount(0);
    await expectNoPageOverflow(page);
    await brief.scrollIntoViewIfNeeded();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: info.outputPath(`authority-service-${scheme}.png`),
      fullPage: true
    });
    await captureAuthorityRegion(
      page,
      brief,
      info.outputPath(`authority-service-section-${scheme}.png`)
    );
    await captureAuthorityRegion(
      page,
      brief
        .getByText('Synthetic recorded service_state', { exact: true })
        .locator('..'),
      info.outputPath(`authority-service-record-section-${scheme}.png`)
    );
    await captureAuthorityRegion(
      page,
      brief
        .getByText('Read-only operating telemetry context', { exact: true })
        .locator('..'),
      info.outputPath(`authority-service-reading-section-${scheme}.png`)
    );
    await page.getByRole('tab', { name: 'Work', exact: true }).click();
    await expect(
      panelRegion(page, 'Work').getByTestId('service-restoration')
    ).toContainText('Synthetic standby unavailable');
    await captureAuthorityRegion(
      page,
      panelRegion(page, 'Work').getByTestId('service-restoration'),
      info.outputPath(`authority-outcome-section-${scheme}.png`)
    );
  });
}

test('O15 no bound tags remains not recorded; operations can record a sourced exception', async ({
  page
}, info) => {
  const server = await mockAuthority(page, authorityProjection(true));
  await openExecutionPanel(page, 'verify');
  const brief = panelRegion(page, 'Verify & Return').getByTestId(
    'service-restoration'
  );
  await expect(
    brief.getByRole('button', {
      name: 'Duty / standby / run telemetry: Not recorded — no reviewed bound tags supplied.'
    })
  ).toHaveText('—');
  await brief
    .getByRole('button', {
      name: 'Record operator duty / standby context',
      exact: true
    })
    .click();
  await selectOption(page, 'Recorded outcome', 'operator recorded');
  await brief
    .getByRole('textbox', { name: 'Recorded fact and conditions', exact: true })
    .fill('Synthetic operator service-state observation');
  await brief
    .getByRole('textbox', {
      name: 'Source document / record reference',
      exact: true
    })
    .fill('Synthetic operating log / entry 16');
  await brief
    .getByRole('textbox', { name: 'standby', exact: true })
    .fill('Synthetic standby unavailable');
  await captureAuthorityRegion(
    page,
    brief.getByTestId('authority-recorder'),
    info.outputPath('authority-service-ready-form-light.png')
  );
  await brief
    .getByRole('button', { name: 'Save attributed record', exact: true })
    .click();
  await expect(brief).toContainText('Authority record saved');
  expect(server.commands[0]).toMatchObject({
    fields: {
      kind: 'service_state',
      outcome: 'operator_recorded',
      standby: 'Synthetic standby unavailable',
      target_id: 18
    }
  });
  expect(server.commands[0].fields).not.toHaveProperty('binding_id');
  await expectNoPageOverflow(page);
  await captureAuthorityRegion(
    page,
    brief,
    info.outputPath('authority-service-recording.png')
  );
  await captureAuthorityRegion(
    page,
    brief.getByTestId('authority-recorder'),
    info.outputPath('authority-service-recorded-form-light.png')
  );
});

test('O15 confirmed telemetry command retains the selected reading time', async ({
  page
}, info) => {
  test.setTimeout(180_000);
  const value = authorityProjection(true);
  value.telemetry = [
    {
      binding_id: 601,
      reading_token: 'c'.repeat(64),
      target_id: 18,
      target_name: 'Synthetic pump',
      tag: 'MOTOR_ON_STATUS',
      path: '/motor/run',
      signal_kind: 'run_context',
      source: 'Synthetic source',
      source_id: 41,
      source_sequence: 10,
      source_active: true,
      value: { value: 0 },
      observed_at: '2026-10-19T06:30:00Z',
      quality: 'good',
      stale: false
    }
  ];
  const server = await mockAuthority(page, value);
  await openExecutionPanel(page, 'verify');
  const brief = panelRegion(page, 'Verify & Return').getByTestId(
    'service-restoration'
  );
  await brief
    .getByRole('button', {
      name: 'Record operator duty / standby context',
      exact: true
    })
    .click();
  await selectOption(page, 'Recorded outcome', 'Reading verified current');
  await brief
    .getByRole('combobox', {
      name: 'Telemetry observation to confirm or correct',
      exact: true
    })
    .click();
  await page.getByRole('option', { name: /MOTOR_ON_STATUS/ }).click();
  await brief
    .getByRole('textbox', { name: 'Recorded fact and conditions', exact: true })
    .fill('Synthetic operator reviewed run context');
  await brief
    .getByRole('textbox', {
      name: 'Source document / record reference',
      exact: true
    })
    .fill('Synthetic operator log');
  await brief
    .getByRole('textbox', { name: 'duty', exact: true })
    .fill('Synthetic duty assignment');
  await captureAuthorityRegion(
    page,
    brief.getByTestId('authority-recorder'),
    info.outputPath('authority-service-telemetry-form-light.png')
  );
  server.value.telemetry = [
    {
      ...server.value.telemetry[0],
      value: { value: 1 },
      source_sequence: 11,
      reading_token: 'd'.repeat(64)
    }
  ];
  await brief
    .getByRole('button', { name: 'Save attributed record', exact: true })
    .click();
  await expect(
    brief.getByRole('button', {
      name: 'Review latest Work after refusal',
      exact: true
    })
  ).toBeVisible();
  expect(server.value.operational).toHaveLength(0);
  await brief
    .getByRole('button', {
      name: 'Review latest Work after refusal',
      exact: true
    })
    .click();
  await brief
    .getByRole('combobox', {
      name: 'Telemetry observation to confirm or correct',
      exact: true
    })
    .click();
  await page.getByRole('option', { name: /MOTOR_ON_STATUS/ }).click();
  await expect(
    brief.getByText('Selected observation', { exact: false })
  ).toContainText('1');
  await brief
    .getByRole('button', { name: 'Save attributed record', exact: true })
    .click();
  await expect(brief).toContainText('Authority record saved');
  expect(server.commands[0].fields).toMatchObject({
    binding_id: 601,
    telemetry_observed_at: '2026-10-19T06:30:00Z',
    telemetry_token: 'c'.repeat(64),
    duty: 'Synthetic duty assignment'
  });
  expect(server.commands[1].fields).toMatchObject({
    telemetry_observed_at: '2026-10-19T06:30:00Z',
    telemetry_token: 'd'.repeat(64)
  });
});

for (const scheme of ['light', 'dark'] as const) {
  test(`O15 rejected acceptance with changed basis stays explicit (${scheme})`, async ({
    page
  }, info) => {
    const value = authorityProjection();
    value.operational = [
      authorityRecord('test_acceptance', {
        id: 10,
        current: false,
        outcome: 'accepted'
      }),
      authorityRecord('test_acceptance', {
        id: 11,
        outcome: 'rejected',
        supersedes: 10,
        basis_state: 'changed_or_unrecorded',
        effective_state: 'expired',
        effective_until: '2026-10-19T09:00:00Z'
      })
    ];
    await mockAuthority(page, value);
    await openExecutionPanel(page, 'verify', scheme);
    const brief = panelRegion(page, 'Verify & Return').getByTestId(
      'service-restoration'
    );
    await expect(brief).toContainText(
      'Test acceptance: rejected · basis changed or unavailable · outside effective window'
    );
    await expect(
      brief.getByText('Test acceptance: recorded', { exact: true })
    ).toHaveCount(0);
    await expect(
      brief.getByText('Test acceptance: accepted', { exact: true })
    ).toHaveCount(0);
    await expect(brief).toContainText('Test acceptance · rejected');
    const basis = await brief
      .getByText('Recorded basis changed or unavailable', { exact: true })
      .evaluate((element) => ({
        contentWidth: element.scrollWidth,
        visibleWidth: element.clientWidth,
        contentHeight: element.scrollHeight,
        visibleHeight: element.clientHeight
      }));
    expect(
      basis.contentWidth,
      'The recorded basis label must remain fully readable'
    ).toBeLessThanOrEqual(basis.visibleWidth + 1);
    expect(basis.contentHeight).toBeLessThanOrEqual(basis.visibleHeight + 1);
    await captureAuthorityRegion(
      page,
      brief,
      info.outputPath(`authority-rejected-basis-${scheme}.png`)
    );
    await expectNoPageOverflow(page);
  });
}

for (const scheme of ['light', 'dark'] as const) {
  test(`O15 reading verification keeps differing typed duty explicit (${scheme})`, async ({
    page
  }, info) => {
    const value = authorityProjection();
    const reading = {
      reading_token: 'e'.repeat(64),
      binding_id: 601,
      target_id: 18,
      target_name: 'Synthetic pump',
      tag: 'SYNTHETIC_DUTY',
      path: '/duty',
      signal_kind: 'duty',
      source: 'Synthetic reviewed source',
      source_id: 41,
      source_sequence: 5,
      source_active: true,
      value: { value: 'Telemetry duty A' },
      observed_at: '2026-10-19T06:30:00Z',
      quality: 'good',
      stale: false
    };
    value.operational = [
      authorityRecord('service_state', {
        outcome: 'confirmed',
        details: { duty: 'Operator assignment B' },
        basis: {
          telemetry: reading,
          telemetry_verification: 'reading_verified_current'
        }
      })
    ];
    await mockAuthority(page, value);
    await openExecutionPanel(page, 'verify', scheme);
    const brief = panelRegion(page, 'Verify & Return').getByTestId(
      'service-restoration'
    );
    await expect(brief).toContainText('Reading verified current');
    await expect(brief).toContainText('duty: Operator assignment B');
    await expect(brief).toContainText(
      'Telemetry basis: SYNTHETIC_DUTY · Telemetry duty A · Synthetic reviewed source'
    );
    await expect(brief).not.toContainText(
      'Duty, standby and redundancy · confirmed'
    );
    await captureAuthorityRegion(
      page,
      brief,
      info.outputPath(`authority-typed-duty-${scheme}.png`)
    );
    await expectNoPageOverflow(page);
  });
}
