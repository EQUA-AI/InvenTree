import { expect, test } from '@playwright/test';
import type {
  CalibrationTraceability,
  RoutineWork
} from '../../lib/types/RoutineWork';
import type { WorkOrderOverview } from '../../lib/types/WorkOrderOverview';
import { nativeBaseline } from '../s0/guards';
import {
  capturePanel,
  expectNoPageOverflow,
  mockExecutionWork,
  onlyReads,
  openExecutionPanel,
  workOrder
} from '../s4/workExecutionFixtures';

nativeBaseline();
async function captureTrace(
  page: import('@playwright/test').Page,
  path: string
) {
  await page.evaluate(() => window.scrollTo(0, 0));
  const clip = await trace(page).boundingBox();
  expect(clip).not.toBeNull();
  await page.screenshot({
    path,
    fullPage: true,
    clip: clip!,
    animations: 'disabled'
  });
}
const first: CalibrationTraceability = {
  id: 401,
  work_order: 73,
  reading: 701,
  revision: 1,
  supersedes: null,
  reference_instrument_id: 'SYN-REFERENCE-001',
  certificate_number: 'SYN-CERT-2026',
  issuing_lab: 'Synthetic calibration laboratory',
  valid_until: '2026-10-18',
  validity_state: 'expired_at_reading',
  validity_timezone: 'Asia/Riyadh',
  reading_recorded_at: '2026-10-19T09:00:00Z',
  certificate_id: 801,
  certificate_snapshot: {
    id: 801,
    filename: 'synthetic-certificate.pdf',
    comment: 'Synthetic fixture'
  },
  certificate_available: true,
  certificate_integrity_state: 'unchanged',
  certificate_url: '/media/attachments/synthetic-certificate.pdf',
  correction_reason: '',
  recorded_by: 11,
  recorded_by_name: 'Synthetic recorder',
  recorded_at: '2026-10-20T09:00:00Z'
};
function work(
  canRecord = false,
  revisions: CalibrationTraceability[] = []
): WorkOrderOverview {
  const routine: RoutineWork = {
    rule: 'routine_work.v1',
    work_order_type: 'calibration',
    procedures_state: 'read',
    readings_state: 'read',
    calibration_traceability_can_record: canRecord,
    applications: [],
    readings: [
      {
        id: 701,
        label: 'Synthetic calibration reference check',
        phase: 'before',
        raw_text: '0 bar',
        value: '0',
        unit: 'bar',
        verification_state: 'pending',
        warnings: [],
        required: true,
        disposition_reason: '',
        expected_min: null,
        expected_max: null,
        step_execution: null,
        recorded_by_name: 'Synthetic technician',
        recorded_at: '2026-10-19T09:00:00Z',
        calibration_traceability: revisions
      }
    ]
  };
  return {
    ...workOrder(),
    work_order_type: 'calibration',
    routine_work: routine
  };
}
function trace(page: import('@playwright/test').Page) {
  return page.getByRole('region', {
    name: 'Reading 701 calibration traceability',
    exact: true
  });
}
async function fill(page: import('@playwright/test').Page) {
  const dialog = page.getByRole('dialog');
  await dialog
    .getByRole('textbox', { name: 'Reference instrument ID' })
    .fill('SYN-REFERENCE-001');
  await dialog
    .getByRole('textbox', { name: 'Certificate number' })
    .fill('SYN-CERT-2026');
  await dialog
    .getByRole('textbox', { name: 'Issuing lab' })
    .fill('Synthetic calibration laboratory');
  await dialog
    .getByRole('combobox', { name: 'Attached certificate', exact: true })
    .click();
  await page.getByRole('option', { name: 'synthetic-certificate.pdf' }).click();
}
async function saveAndRefresh(
  page: import('@playwright/test').Page,
  label: string,
  revision: number
) {
  const receipt = page.waitForResponse(
    (r) =>
      r
        .url()
        .endsWith('/api/kanban/work-orders/73/calibration-traceability/701/') &&
      r.request().method() === 'POST'
  );
  const refresh = page.waitForResponse(
    (r) =>
      r.url().endsWith('/api/kanban/work-orders/73/overview/') &&
      r.request().method() === 'GET'
  );
  await page
    .getByRole('dialog')
    .getByRole('button', { name: label, exact: true })
    .click();
  const saved = await receipt;
  expect(saved.status()).toBe(200);
  expect((await saved.json()).traceability.revision).toBe(revision);
  expect((await refresh).status()).toBe(200);
  await expect(trace(page)).toContainText(`Revision ${revision}`);
  await expect(page.getByRole('dialog')).toHaveCount(0);
}
async function files(page: import('@playwright/test').Page) {
  await page.route('**/api/attachment/**', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    await route.fulfill({
      json: {
        count: 1,
        next: null,
        previous: null,
        results: [
          {
            pk: 801,
            model_type: 'workorder',
            model_id: 73,
            filename: 'synthetic-certificate.pdf',
            attachment: '/media/attachments/synthetic-certificate.pdf',
            comment: 'Synthetic file'
          }
        ]
      }
    });
  });
}
for (const scheme of ['light', 'dark'] as const) {
  test(`O20 viewer sees explicit missing traceability and cannot record (${scheme})`, async ({
    page
  }, info) => {
    const requests = await mockExecutionWork(page, work());
    await openExecutionPanel(page, 'work', scheme);
    await expect(trace(page)).toContainText(
      'Calibration traceability not recorded'
    );
    await expect(
      trace(page).getByRole('button', {
        name: 'Record traceability',
        exact: true
      })
    ).toHaveCount(0);
    await expectNoPageOverflow(page);
    await capturePanel(page, info.outputPath(`missing-${scheme}.png`));
    await captureTrace(page, info.outputPath(`missing-${scheme}-detail.png`));
    expect(onlyReads(requests)).toBe(true);
  });
  test(`O20 retained expired and corrected revisions are distinct (${scheme})`, async ({
    page
  }, info) => {
    const corrected: CalibrationTraceability = {
      ...first,
      id: 402,
      revision: 2,
      supersedes: 401,
      valid_until: null,
      validity_state: 'unknown',
      correction_reason: 'Synthetic date transcription correction',
      recorded_by_name: 'Synthetic correcting recorder'
    };
    await mockExecutionWork(page, work(false, [corrected, first]));
    await openExecutionPanel(page, 'work', scheme);
    await expect(trace(page)).toContainText(
      'Certificate date validity unknown'
    );
    await expect(trace(page)).toContainText('Valid untilUnknown');
    await trace(page)
      .getByRole('button', { name: 'Earlier traceability revisions (1)' })
      .click();
    await expect(trace(page)).toContainText(
      'Certificate expired at reading time'
    );
    await expect(trace(page)).toContainText('SYN-REFERENCE-001');
    await expect(trace(page)).toContainText('Synthetic correcting recorder');
    await expect(trace(page)).toContainText('Synthetic recorder');
    await expect(
      trace(page)
        .getByRole('link', { name: 'Open attached certificate' })
        .first()
    ).toHaveAttribute('href', new URL(first.certificate_url!, page.url()).href);
    await expect(trace(page)).toContainText(
      'This is not calibration acceptance'
    );
    await expectNoPageOverflow(page);
    await capturePanel(page, info.outputPath(`history-${scheme}.png`));
    await captureTrace(page, info.outputPath(`history-${scheme}-detail.png`));
  });
  test(`O20 authorized record and correction refresh the mounted reading (${scheme})`, async ({
    page
  }, info) => {
    const value = work(true);
    await mockExecutionWork(page, value);
    await files(page);
    const bodies: Record<string, unknown>[] = [];
    await page.route(
      '**/api/kanban/work-orders/73/calibration-traceability/701/',
      async (route) => {
        const body = route.request().postDataJSON();
        bodies.push(body);
        const row: CalibrationTraceability = {
          ...first,
          id: 400 + bodies.length,
          revision: bodies.length,
          supersedes: bodies.length === 2 ? 401 : null,
          valid_until: body.valid_until,
          validity_state: body.valid_until
            ? 'within_date_at_reading'
            : 'unknown',
          certificate_number: body.certificate_number,
          correction_reason: body.correction_reason
        };
        value.routine_work!.readings[0].calibration_traceability!.unshift(row);
        await route.fulfill({
          json: {
            traceability: row,
            lifecycle_version: value.lifecycle_version
          }
        });
      }
    );
    await openExecutionPanel(page, 'work', scheme, false, 'editor');
    await trace(page)
      .getByRole('button', { name: 'Record traceability', exact: true })
      .focus();
    await page.keyboard.press('Enter');
    await fill(page);
    await expect(
      page.getByRole('button', { name: 'Close calibration traceability form' })
    ).toBeVisible();
    await expect(page.getByRole('dialog')).toContainText(
      'This does not accept the calibration'
    );
    await expectNoPageOverflow(page);
    await page.screenshot({
      path: info.outputPath(`record-form-${scheme}.png`),
      fullPage: true
    });
    await page.getByRole('dialog').screenshot({
      path: info.outputPath(`record-form-${scheme}-detail.png`)
    });
    await saveAndRefresh(page, 'Record traceability', 1);
    await expect(trace(page)).toContainText('Revision 1');
    await expect(trace(page)).toContainText(
      'Certificate date validity unknown'
    );
    expect(bodies[0]).toMatchObject({
      operation: 'record',
      expected_version: value.lifecycle_version,
      expected_revision: 0,
      reference_instrument_id: 'SYN-REFERENCE-001',
      certificate_number: 'SYN-CERT-2026',
      issuing_lab: 'Synthetic calibration laboratory',
      valid_until: null,
      certificate_id: 801
    });
    await trace(page)
      .getByRole('button', { name: 'Correct traceability' })
      .click();
    const dialog = page.getByRole('dialog');
    await dialog
      .getByRole('textbox', { name: 'Certificate number' })
      .fill('SYN-CORRECTED');
    await dialog
      .getByRole('textbox', { name: 'Valid until', exact: true })
      .fill('2026-10-20');
    await dialog
      .getByRole('textbox', { name: 'Correction reason' })
      .fill('Synthetic transcription correction');
    await saveAndRefresh(page, 'Save new revision', 2);
    await expect(trace(page)).toContainText('Revision 2');
    await expect(trace(page)).toContainText(
      'Certificate within recorded date at reading time'
    );
    await trace(page)
      .getByRole('button', { name: 'Earlier traceability revisions (1)' })
      .click();
    await expect(trace(page)).toContainText('SYN-CERT-2026');
    expect(bodies[1]).toMatchObject({
      operation: 'correct',
      expected_revision: 1,
      certificate_number: 'SYN-CORRECTED',
      valid_until: '2026-10-20',
      correction_reason: 'Synthetic transcription correction'
    });
    expect(bodies[0].idempotency_key).not.toEqual(bodies[1].idempotency_key);
    await capturePanel(page, info.outputPath(`saved-${scheme}.png`));
    await captureTrace(page, info.outputPath(`saved-${scheme}-detail.png`));
  });
}
test('O20 uncertain response retries identical intent; denial never shows saved', async ({
  page
}) => {
  await mockExecutionWork(page, work(true));
  await files(page);
  const bodies: unknown[] = [];
  await page.route(
    '**/api/kanban/work-orders/73/calibration-traceability/701/',
    async (route) => {
      bodies.push(route.request().postDataJSON());
      await route.fulfill({
        status: bodies.length === 1 ? 503 : 403,
        json: { detail: 'Synthetic failure' }
      });
    }
  );
  await openExecutionPanel(page, 'work', 'light', false, 'editor');
  await trace(page)
    .getByRole('button', { name: 'Record traceability', exact: true })
    .click();
  await fill(page);
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Record traceability', exact: true })
    .click();
  await expect(page.getByRole('dialog')).toContainText('Save outcome unknown');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(
    page
      .getByRole('dialog')
      .getByRole('textbox', { name: 'Reference instrument ID' })
  ).toBeDisabled();
  await page.getByRole('button', { name: 'Retry same command' }).click();
  await expect(page.getByRole('dialog')).toContainText(
    'This command is not permitted'
  );
  expect(bodies[0]).toEqual(bodies[1]);
  await expect(trace(page)).toContainText(
    'Calibration traceability not recorded'
  );
});
test('O20 stale version keeps correction unsaved and requires refresh', async ({
  page
}) => {
  await mockExecutionWork(page, work(true, [first]));
  await files(page);
  await page.route(
    '**/api/kanban/work-orders/73/calibration-traceability/701/',
    (route) => route.fulfill({ status: 409, json: { code: 'STALE_VERSION' } })
  );
  await openExecutionPanel(page, 'work', 'dark', false, 'editor');
  await trace(page)
    .getByRole('button', { name: 'Correct traceability' })
    .click();
  await page
    .getByRole('textbox', { name: 'Correction reason' })
    .fill('Synthetic correction');
  await page.getByRole('button', { name: 'Save new revision' }).click();
  await expect(page.getByRole('dialog')).toContainText(
    'Close this form and refresh the Work'
  );
  await expect(
    page.getByRole('button', { name: 'Save new revision' })
  ).toBeDisabled();
  await expect(trace(page)).toContainText('Revision 1');
});
test('O20 foreign revision is hidden and deleted certificate is explicit', async ({
  page
}) => {
  await mockExecutionWork(
    page,
    work(false, [
      {
        ...first,
        certificate_available: false,
        certificate_integrity_state: 'unavailable',
        certificate_id: null,
        certificate_url: null
      },
      {
        ...first,
        id: 999,
        work_order: 74,
        reference_instrument_id: 'FOREIGN-INSTRUMENT'
      }
    ])
  );
  await openExecutionPanel(page);
  await expect(trace(page)).toContainText('Attached certificate unavailable');
  await expect(trace(page)).not.toContainText('FOREIGN-INSTRUMENT');
  await expect(
    trace(page).getByRole('link', { name: 'Open attached certificate' })
  ).toHaveCount(0);
});
test('O20 certificate list failure and empty list cannot be recorded', async ({
  page
}) => {
  await mockExecutionWork(page, work(true));
  let count = 0;
  await page.route('**/api/attachment/**', async (route) => {
    if (
      !new URL(route.request().url()).searchParams.has('limit') ||
      new URL(route.request().url()).searchParams.get('limit') === '1'
    )
      return route.fulfill({ json: { count: 0, results: [], next: null } });
    count++;
    await route.fulfill(
      count === 1
        ? { status: 503, json: {} }
        : { json: { count: 0, results: [], next: null } }
    );
  });
  await openExecutionPanel(page, 'work', 'light', false, 'editor');
  await trace(page)
    .getByRole('button', { name: 'Record traceability', exact: true })
    .click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('Certificate list unavailable');
  await dialog
    .getByRole('button', { name: 'Refresh certificate list' })
    .click();
  await expect(dialog).toContainText(
    'No uploaded certificate files returned for this Work'
  );
  await expect(
    dialog.getByRole('button', { name: 'Record traceability', exact: true })
  ).toBeDisabled();
});

test('O20 missing traceability provider is unavailable and cannot authorize a writer', async ({
  page
}) => {
  const value = work(true);
  delete value.routine_work!.readings[0].calibration_traceability;
  await mockExecutionWork(page, value);
  await openExecutionPanel(page);
  await expect(trace(page)).toContainText(
    'Calibration traceability unavailable'
  );
  await expect(
    trace(page).getByRole('button', {
      name: 'Record traceability',
      exact: true
    })
  ).toHaveCount(0);
});
test('O20 certificate selector reads later native attachment pages', async ({
  page
}) => {
  await mockExecutionWork(page, work(true));
  const offsets: string[] = [];
  await page.route('**/api/attachment/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('limit') === '1')
      return route.fulfill({ json: { count: 2, results: [], next: null } });
    const offset = url.searchParams.get('offset') ?? '0';
    offsets.push(offset);
    await route.fulfill({
      json:
        offset === '0'
          ? {
              count: 2,
              next: 'http://127.0.0.1:15220/api/attachment/?offset=1',
              results: [
                {
                  pk: 800,
                  attachment: null,
                  filename: '',
                  link: 'https://example.invalid/link'
                }
              ]
            }
          : {
              count: 2,
              next: null,
              results: [
                {
                  pk: 801,
                  attachment: '/media/synthetic-certificate.pdf',
                  filename: 'synthetic-certificate.pdf'
                }
              ]
            }
    });
  });
  await openExecutionPanel(page, 'work', 'light', false, 'editor');
  await trace(page)
    .getByRole('button', { name: 'Record traceability', exact: true })
    .click();
  await page
    .getByRole('combobox', { name: 'Attached certificate', exact: true })
    .click();
  await expect(
    page.getByRole('option', { name: 'synthetic-certificate.pdf' })
  ).toBeVisible();
  expect(offsets).toEqual(['0', '1']);
});

test('O20 long recorded identities and file names stay readable at the native width', async ({
  page
}, info) => {
  const long = {
    ...first,
    reference_instrument_id: `SYN-${'R'.repeat(196)}`,
    certificate_number: `SYN-${'C'.repeat(196)}`,
    issuing_lab: `Synthetic laboratory ${'L'.repeat(177)}`,
    certificate_snapshot: {
      ...first.certificate_snapshot,
      filename: `attachments/${'f'.repeat(80)}.pdf`
    }
  };
  const requests = await mockExecutionWork(page, work(false, [long]));
  await openExecutionPanel(page);
  await expect(trace(page)).toContainText(long.reference_instrument_id);
  await expect(trace(page)).toContainText(long.certificate_number);
  await expect(trace(page)).toContainText(long.issuing_lab);
  await expectNoPageOverflow(page);
  await captureTrace(page, info.outputPath('long-recorded-identities.png'));
  expect(onlyReads(requests)).toBe(true);
});

test('O20 correction keeps its opening versions when another client updates Work', async ({
  page
}) => {
  const value = work(true, [first]);
  const openingVersion = value.lifecycle_version;
  await mockExecutionWork(page, value);
  await files(page);
  let submitted: Record<string, unknown> | undefined;
  const remote = {
    ...first,
    id: 402,
    revision: 2,
    supersedes: first.id,
    certificate_number: 'SYN-REMOTE-CORRECTION'
  };
  await page.route(
    '**/api/kanban/work-orders/73/calibration-traceability/701/',
    async (route) => {
      submitted = route.request().postDataJSON();
      if (
        submitted?.expected_version !== value.lifecycle_version ||
        submitted?.expected_revision !== remote.revision
      ) {
        await route.fulfill({ status: 409, json: { code: 'STALE_VERSION' } });
      } else {
        await route.fulfill({
          json: {
            traceability: { ...remote, id: 403, revision: 3 },
            lifecycle_version: value.lifecycle_version
          }
        });
      }
    }
  );
  await openExecutionPanel(page, 'work', 'light', false, 'editor');
  await trace(page)
    .getByRole('button', { name: 'Correct traceability' })
    .click();
  const dialog = page.getByRole('dialog');
  await dialog
    .getByRole('textbox', { name: 'Correction reason' })
    .fill('Reviewing the opening revision');
  // Simulate an external correction, then the native background query refresh.
  value.lifecycle_version += 1;
  value.routine_work!.readings[0].calibration_traceability = [remote, first];
  const refreshed = page.waitForResponse((r) =>
    r.url().endsWith('/api/kanban/work-orders/73/overview/')
  );
  await page
    .getByRole('button', { name: 'Refresh fixture', exact: true })
    .evaluate((button) => (button as HTMLButtonElement).click());
  await refreshed;
  await expect(page.getByTestId('routine-work')).toContainText(
    'SYN-REMOTE-CORRECTION'
  );
  await expect(
    dialog.getByRole('textbox', { name: 'Certificate number' })
  ).toHaveValue('SYN-CERT-2026');
  const response = page.waitForResponse((r) =>
    r.url().endsWith('/api/kanban/work-orders/73/calibration-traceability/701/')
  );
  await dialog.getByRole('button', { name: 'Save new revision' }).click();
  const saved = await response;
  expect(submitted).toMatchObject({
    expected_version: openingVersion,
    expected_revision: 1,
    certificate_number: 'SYN-CERT-2026'
  });
  expect(saved.status()).toBe(409);
  await expect(dialog).toContainText('The Work or traceability changed');
  await expect(
    dialog.getByRole('button', { name: 'Save new revision' })
  ).toBeDisabled();
});

for (const scheme of ['light', 'dark'] as const) {
  test(`O20 changed certificate has no original link or correction preselection (${scheme})`, async ({
    page
  }, info) => {
    const changed = {
      ...first,
      certificate_available: false,
      certificate_integrity_state: 'changed' as const,
      certificate_url: null
    };
    await mockExecutionWork(page, work(true, [changed]));
    await files(page);
    await openExecutionPanel(page, 'work', scheme, false, 'editor');
    await expect(trace(page)).toContainText(
      'Certificate changed since recorded'
    );
    await expect(
      trace(page).getByRole('link', { name: 'Open attached certificate' })
    ).toHaveCount(0);
    await captureTrace(
      page,
      info.outputPath(`changed-certificate-${scheme}-detail.png`)
    );
    await trace(page)
      .getByRole('button', { name: 'Correct traceability' })
      .click();
    const dialog = page.getByRole('dialog');
    await dialog
      .getByRole('textbox', { name: 'Correction reason' })
      .fill('Select a separately uploaded certificate');
    await expect(
      dialog.getByRole('combobox', {
        name: 'Attached certificate',
        exact: true
      })
    ).toHaveValue('');
    await expect(
      dialog.getByRole('button', { name: 'Save new revision' })
    ).toBeDisabled();
    await dialog.screenshot({
      path: info.outputPath(`changed-certificate-${scheme}-form.png`),
      animations: 'disabled'
    });
    const save = dialog.getByRole('button', { name: 'Save new revision' });
    await save.scrollIntoViewIfNeeded();
    await expect(save).toBeVisible();
    await page.screenshot({
      path: info.outputPath(`changed-certificate-${scheme}-actions.png`),
      animations: 'disabled'
    });
    await expectNoPageOverflow(page);
    await dialog
      .getByRole('combobox', { name: 'Attached certificate', exact: true })
      .click();
    await page
      .getByRole('option', { name: 'synthetic-certificate.pdf' })
      .click();
    await expect(
      dialog.getByRole('button', { name: 'Save new revision' })
    ).toBeEnabled();
  });
}

test('O20 legacy certificate without byte identity is unknown and cannot be preselected', async ({
  page
}) => {
  const legacy = { ...first };
  delete legacy.certificate_integrity_state;
  await mockExecutionWork(page, work(true, [legacy]));
  await files(page);
  await openExecutionPanel(page, 'work', 'light', false, 'editor');
  await expect(trace(page)).toContainText(
    'Certificate byte identity not recorded'
  );
  await expect(
    trace(page).getByRole('link', { name: 'Open attached certificate' })
  ).toHaveCount(0);
  await trace(page)
    .getByRole('button', { name: 'Correct traceability' })
    .click();
  await expect(
    page
      .getByRole('dialog')
      .getByRole('combobox', { name: 'Attached certificate', exact: true })
  ).toHaveValue('');
});
