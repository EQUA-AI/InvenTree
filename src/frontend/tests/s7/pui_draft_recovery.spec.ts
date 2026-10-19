import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import {
  closeoutWork,
  containment,
  openCloseout,
  proposal,
  story,
  values
} from '../s6/closeoutFixtures';

nativeBaseline();
async function reviewEditor(page: import('@playwright/test').Page) {
  await page.getByText('Edit or add to the capture', { exact: true }).click();
  await page
    .getByTestId('closeout-capture-disclosure')
    .getByText('Review', { exact: true })
    .click();
}
for (const scheme of ['light', 'dark'] as const) {
  test(`capture conflict compares latest and draft before deliberate merge · ${scheme}`, async ({
    page
  }, info) => {
    await closeoutWork(page);
    let changed = false;
    const bodies: any[] = [];
    await page.route('**/closeout/captures/', (route) =>
      route.fulfill({
        json: [{ ...story, decision_version: (changed ? 'b' : 'a').repeat(64) }]
      })
    );
    await page.route('**/closeout/captures/12/proposal/', (route) =>
      route.fulfill({
        json: changed
          ? {
              ...proposal,
              decision_version: 'b'.repeat(64),
              decisions: proposal.decisions.map((d) =>
                d.field_path === 'result'
                  ? {
                      ...d,
                      final_value:
                        'Newer saved observation by another technician'
                    }
                  : d
              )
            }
          : proposal
      })
    );
    await page.route('**/closeout/captures/12/decisions/', (route) => {
      bodies.push(route.request().postDataJSON());
      changed = true;
      return route.fulfill({
        status: 409,
        json: { code: 'CAPTURE_STALE_REVISION' }
      });
    });
    await openCloseout(page, scheme);
    await reviewEditor(page);
    const field = page.getByRole('textbox', { name: 'Result', exact: true });
    await field.fill('My retained draft observation');
    await page
      .getByRole('button', { name: 'Record decisions', exact: true })
      .click();
    const recovery = page.getByTestId('draft-recovery');
    await expect(recovery).toContainText('record changed');
    await recovery
      .getByRole('button', { name: 'Review latest record', exact: true })
      .click();
    await expect(recovery).toContainText('Newer saved observation');
    await expect(recovery).toContainText('My retained draft observation');
    await containment(page);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: info.outputPath(`capture-conflict-${scheme}.png`),
      fullPage: true
    });
    expect(bodies).toHaveLength(1);
    await recovery
      .getByRole('button', { name: 'Keep draft on reviewed version' })
      .click();
    await expect(field).toHaveValue('My retained draft observation');
    await page
      .getByRole('button', { name: 'Record decisions', exact: true })
      .click();
    await expect.poll(() => bodies.length).toBe(2);
    expect(bodies[0].expected_decision_version).toBe('a'.repeat(64));
    expect(bodies[1].expected_decision_version).toBe('b'.repeat(64));
    expect(bodies[1].idempotency_key).not.toBe(bodies[0].idempotency_key);
  });
}
test('lost narrative response retains draft through panel switch and replays exact command', async ({
  page
}, info) => {
  await closeoutWork(page, { captures: [] });
  const bodies: any[] = [];
  await page.route('**/closeout/captures/', (route) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: [] });
    const body = route.request().postDataJSON();
    bodies.push(body);
    if (bodies.length === 1) return route.abort('failed');
    return route.fulfill({
      json: {
        work_order_id: 73,
        command: 'closeout_capture',
        event_id: 111,
        lifecycle_version: body.expected_version,
        metadata: { capture_id: 12 },
        idempotency_key: body.idempotency_key
      }
    });
  });
  await openCloseout(page);
  await page.getByText('Edit or add to the capture', { exact: true }).click();
  await page
    .getByRole('textbox', { name: 'What did you do?' })
    .fill('Retained narrative after a lost response');
  await page
    .getByRole('button', { name: 'Capture narrative', exact: true })
    .click();
  await expect(page.getByTestId('draft-recovery')).toContainText(
    'Save not confirmed'
  );
  await page.getByRole('tab', { name: 'History', exact: true }).click();
  await page.getByRole('tab', { name: 'Closeout', exact: true }).click();
  await expect(
    page.getByRole('textbox', { name: 'What did you do?' })
  ).toHaveValue('Retained narrative after a lost response');
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath('narrative-uncertain.png'),
    fullPage: true
  });
  await page.reload();
  await expect(page.getByTestId('draft-recovery')).toContainText(
    'Retained narrative after a lost response'
  );
  await expect(
    page.getByRole('textbox', { name: 'What did you do?' })
  ).toHaveValue('Retained narrative after a lost response');
  await page
    .getByRole('button', { name: 'Check original save', exact: true })
    .click();
  await expect(page.getByTestId('draft-recovery')).toHaveCount(0);
  expect(bodies).toHaveLength(2);
  expect(bodies[1]).toEqual(bodies[0]);
});

test('failed latest read preserves draft without showing protected comparison', async ({
  page
}) => {
  await closeoutWork(page);
  await page.route('**/closeout/captures/12/decisions/', (route) =>
    route.fulfill({ status: 409, json: { code: 'CAPTURE_STALE_REVISION' } })
  );
  await openCloseout(page);
  await reviewEditor(page);
  await page
    .getByRole('textbox', { name: 'Result', exact: true })
    .fill('Retained after refusal');
  await page
    .getByRole('button', { name: 'Record decisions', exact: true })
    .click();
  await page.route('**/api/kanban/work-orders/73/overview/', (route) =>
    route.fulfill({ status: 404, json: { detail: 'Not found' } })
  );
  const recovery = page.getByTestId('draft-recovery');
  await recovery
    .getByRole('button', { name: 'Review latest record', exact: true })
    .click();
  await expect(recovery).toContainText('Latest record unavailable');
  await expect(recovery).toContainText('Retained after refusal');
  await expect(
    recovery.getByText('Latest saved record', { exact: true })
  ).toHaveCount(0);
  await expect(
    recovery.getByRole('button', { name: 'Keep draft on reviewed version' })
  ).toHaveCount(0);
});

test('reading timeout retains fields and locks unsafe append retry', async ({
  page
}, info) => {
  const { commands } = await closeoutWork(page);
  await page.route('**/closeout/readings/', (route) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: [] });
    commands.push({
      path: 'readings',
      method: 'POST',
      body: route.request().postDataJSON()
    });
    return route.abort('failed');
  });
  await openCloseout(page);
  await page.getByRole('tab', { name: 'Verify & Return', exact: true }).click();
  await page
    .getByRole('textbox', { name: 'Label', exact: true })
    .fill('Synthetic reading');
  await page
    .getByRole('textbox', { name: 'Value', exact: true })
    .fill('unknown observation');
  await page.getByRole('button', { name: 'Add reading', exact: true }).click();
  await expect(
    page.getByText('Reading save not confirmed.', { exact: false })
  ).toBeVisible();
  await expect(
    page.getByRole('textbox', { name: 'Value', exact: true })
  ).toHaveValue('unknown observation');
  await expect(
    page.getByRole('button', { name: 'Add reading', exact: true })
  ).toBeDisabled();
  await containment(page);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath('reading-uncertain.png'),
    fullPage: true
  });
  expect(commands).toHaveLength(1);
});

test('ordinary refusal keeps the complete account fields and original command', async ({
  page
}) => {
  await closeoutWork(page);
  const bodies: any[] = [];
  await page.route('**/closeout/captures/12/decisions/', (route) => {
    bodies.push(route.request().postDataJSON());
    return route.fulfill({ status: 403, json: { code: 'PERMISSION_DENIED' } });
  });
  await openCloseout(page);
  await reviewEditor(page);
  await page
    .getByRole('textbox', { name: 'Action taken', exact: true })
    .fill('Draft action');
  await page
    .getByRole('textbox', { name: 'Result', exact: true })
    .fill('Draft result');
  await page
    .getByRole('button', { name: 'Record decisions', exact: true })
    .click();
  await expect(page.getByTestId('draft-recovery')).toContainText(
    'Save refused'
  );
  await expect(
    page.getByRole('textbox', { name: 'Action taken', exact: true })
  ).toHaveValue('Draft action');
  await page
    .getByRole('button', { name: 'Check original save', exact: true })
    .click();
  await expect.poll(() => bodies.length).toBe(2);
  expect(bodies[1]).toEqual(bodies[0]);
  expect(bodies[0].decisions).toContainEqual({
    field_path: 'verification_summary',
    decision: 'accepted'
  });
  expect(values.verification_summary).toBeTruthy();
});

test('background source failure retains draft while denied refresh hides Work', async ({
  page
}, info) => {
  await closeoutWork(page, { captures: [] });
  await openCloseout(page);
  await page.getByText('Edit or add to the capture', { exact: true }).click();
  const narrative = page.getByRole('textbox', { name: 'What did you do?' });
  await narrative.fill('Draft survives the source outage');
  let status = 500;
  await page.route('**/api/kanban/work-orders/73/overview/', (route) =>
    route.fulfill({ status, json: { detail: 'Unavailable' } })
  );
  await page
    .getByRole('button', { name: 'Refresh fixture', exact: true })
    .click();
  await expect(
    page.getByText('Latest Work unavailable', { exact: true })
  ).toBeVisible();
  await expect(narrative).toHaveValue('Draft survives the source outage');
  await containment(page);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath('last-known-with-draft.png'),
    fullPage: true
  });
  status = 404;
  await page.getByRole('button', { name: 'Retry Work', exact: true }).click();
  await expect(
    page.getByText('Work order unavailable', { exact: true })
  ).toBeVisible();
  await expect(narrative).toHaveCount(0);
});

test('parts disposition and reason survive step switches and a stale row', async ({
  page
}, info) => {
  await closeoutWork(page);
  const row = {
    id: 81,
    source: 'narrative',
    state: 'candidate',
    candidate_text: 'Synthetic unlinked spare',
    planned_quantity: null,
    issued_quantity: null,
    used_quantity: null,
    disposition: '',
    version: 1
  };
  const bodies: any[] = [];
  await page.route('**/closeout/part-usage/', (route) =>
    route.fulfill({ json: [row] })
  );
  await page.route('**/closeout/part-usage/81/resolve/', (route) => {
    bodies.push(route.request().postDataJSON());
    row.version = 2;
    return route.fulfill({
      status: 400,
      json: { code: 'RECONCILIATION_INVALID' }
    });
  });
  await openCloseout(page);
  await page.getByText('Edit or add to the capture', { exact: true }).click();
  const editor = page.getByTestId('closeout-capture-disclosure');
  await editor.getByText('Parts', { exact: true }).click();
  await editor.getByPlaceholder('Disposition', { exact: true }).click();
  await page.getByRole('option', { name: 'dismissed', exact: true }).click();
  await editor
    .getByPlaceholder('Reason', { exact: true })
    .fill('Retained disposition reason');
  await editor.getByText('Review', { exact: true }).click();
  await editor.getByText('Parts', { exact: true }).click();
  await expect(editor.getByPlaceholder('Reason', { exact: true })).toHaveValue(
    'Retained disposition reason'
  );
  await editor.getByRole('button', { name: 'Resolve', exact: true }).click();
  const recovery = page.getByTestId('draft-recovery');
  await expect(recovery).toContainText('Retained disposition reason');
  await recovery
    .getByRole('button', { name: 'Review latest record', exact: true })
    .click();
  await expect(recovery).toContainText('Row version: 2');
  await containment(page);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath('parts-conflict.png'),
    fullPage: true
  });
  await recovery
    .getByRole('button', { name: 'Keep draft on reviewed version' })
    .click();
  await editor.getByRole('button', { name: 'Resolve', exact: true }).click();
  await expect.poll(() => bodies.length).toBe(2);
  expect(bodies.map((body) => body.expected_row_version)).toEqual([1, 2]);
});

test('failed transcript review survives dismissing and reopening dictation', async ({
  page
}) => {
  await closeoutWork(page, { captures: [] });
  await page.route('**/api/voice/captures/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/revise/')) return route.abort('failed');
    return route.fulfill({
      json: {
        id: 'synthetic-voice',
        state: path.endsWith('/cancel/') ? 'canceled' : 'active',
        revisions: []
      }
    });
  });
  await openCloseout(page);
  await page.getByText('Edit or add to the capture', { exact: true }).click();
  await page.getByTestId('dictate-closeout').click();
  await page.getByTestId('dictate-consent').click();
  await page
    .getByTestId('dictate-transcript')
    .fill('Retained transcript after review failure');
  await page.getByTestId('dictate-submit-revision').click();
  await expect(page.getByTestId('dictate-transcript')).toHaveValue(
    'Retained transcript after review failure'
  );
  await expect(
    page.getByTestId('dictate-closeout-modal').getByRole('alert')
  ).toBeVisible();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Close dictation', exact: true })
    .click();
  await page.getByTestId('dictate-closeout').click();
  await expect(
    page.getByText('Your transcript draft is retained.', { exact: false })
  ).toBeVisible();
  await page.getByTestId('dictate-consent').click();
  await expect(page.getByTestId('dictate-transcript')).toHaveValue(
    'Retained transcript after review failure'
  );
});

test('deviation refusal allows correction but lost response locks original draft', async ({
  page
}, info) => {
  const state = await closeoutWork(page);
  state.setOverview({
    prepared_package: {
      rule: 'prepared_package.v1',
      scopes: [],
      applications: [],
      deviations: [],
      procedures_state: 'read',
      can_record_deviation: true,
      physical_authority: { available: false, reason: 'no_provider' },
      affected_dependencies: { available: false, reason: 'no_provider' }
    }
  });
  const bodies: any[] = [];
  await page.route('**/api/tasks/work-orders/73/deviations/', (route) => {
    bodies.push(route.request().postDataJSON());
    return bodies.length === 1
      ? route.fulfill({ status: 403, json: { detail: 'Refused' } })
      : route.abort('failed');
  });
  await openCloseout(page);
  await page.getByRole('tab', { name: 'Plan & Safety', exact: true }).click();
  await page
    .getByRole('button', { name: 'Record a changed condition', exact: true })
    .click();
  const field = page.getByRole('textbox', {
    name: 'Changed condition and reason',
    exact: true
  });
  await field.fill('Original changed condition');
  await page
    .getByRole('button', { name: 'Save deviation', exact: true })
    .click();
  await expect(
    page.getByText('Save refused. Your text is retained.', { exact: false })
  ).toBeVisible();
  await field.fill('Corrected changed condition');
  await page
    .getByRole('button', { name: 'Save deviation', exact: true })
    .click();
  await expect(
    page.getByText('Save not confirmed. Your text is retained.', {
      exact: false
    })
  ).toBeVisible();
  await expect(field).toHaveValue('Corrected changed condition');
  await expect(
    page.getByRole('button', { name: 'Save deviation', exact: true })
  ).toBeDisabled();
  await containment(page);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath('deviation-uncertain.png'),
    fullPage: true
  });
  expect(bodies).toHaveLength(2);
});

test('proposal fields keep their own revision when the capture list is newer', async ({
  page
}) => {
  await closeoutWork(page, {
    captures: [{ ...story, decision_version: 'b'.repeat(64) }]
  });
  const bodies: any[] = [];
  await page.route('**/closeout/captures/12/decisions/', (route) => {
    bodies.push(route.request().postDataJSON());
    return route.fulfill({
      status: 409,
      json: { code: 'CAPTURE_STALE_REVISION' }
    });
  });
  await openCloseout(page);
  await reviewEditor(page);
  await page
    .getByRole('textbox', { name: 'Result', exact: true })
    .fill('Retained snapshot draft');
  await page
    .getByRole('button', { name: 'Record decisions', exact: true })
    .click();
  await expect(page.getByTestId('draft-recovery')).toContainText(
    'record changed'
  );
  expect(bodies).toHaveLength(1);
  expect(bodies[0].expected_decision_version).toBe('a'.repeat(64));
});
