import { type Page, expect, test } from '@playwright/test';
import type { InvestigationRequest } from '../../lib/types/WorkExecution';
import type { ObservationIntent } from '../../src/pages/tasks/workorders/execution/observationCapture';
import { nativeBaseline } from '../s0/guards';
import {
  capturePanel,
  expectNoPageOverflow,
  mockExecutionWork,
  openExecutionPanel,
  savedRequest,
  workOrder
} from './workExecutionFixtures';

nativeBaseline();

// The local entry 10:00 is asserted as 07:00Z, so pin the browser's zone.
test.describe('O15 observation timing', () => {
  test.use({ timezoneId: 'Asia/Riyadh' });
  for (const scheme of ['light', 'dark'] as const) {
    test(`O15 observation media retains supplied recording timing (${scheme})`, async ({
      page
    }, info) => {
      const { row } = await setup(page);
      let body: ObservationIntent | undefined;
      await page.route('**/investigation-requests/401/', async (route) => {
        body = route.request().postDataJSON();
        const result = saved(row, body!);
        result.request.artifacts[0].originals = [
          {
            id: 91,
            filename: 'Synthetic observation.png',
            content_type: 'image/png',
            content_hash: 'd'.repeat(64),
            byte_size: 12,
            started_at: '2026-10-20T07:00:00Z',
            duration_ms: 1200,
            url: '/api/tasks/work-orders/73/capture-media/91/'
          }
        ];
        await route.fulfill({
          json: { request: result.request, artifact_id: result.artifact_id }
        });
      });
      await open(page, scheme);
      const editor = draft(page);
      await editor
        .getByRole('textbox', { name: 'Observed fact', exact: true })
        .fill('Synthetic retained observation');
      await editor
        .getByLabel('Recording start time (local)', { exact: true })
        .fill('2026-10-20T10:00');
      await editor
        .getByLabel('Recording duration (ms)', { exact: true })
        .fill('1200');
      await editor
        .getByLabel('Existing Work attachment (optional)', { exact: true })
        .click();
      await page
        .getByRole('option', {
          name: 'Synthetic observation.png · 901',
          exact: true
        })
        .click();
      await editor
        .getByRole('button', {
          name: 'Save observation to request',
          exact: true
        })
        .click();
      await expect(editor).toContainText('Observation saved');
      expect(body!.fields.payload).toMatchObject({
        recording_started_at: '2026-10-20T07:00:00.000Z',
        recording_duration_ms: 1200
      });
      await expect(editor).toContainText(
        'Download original: Synthetic observation.png'
      );
      await expectNoPageOverflow(page);
      await capturePanel(
        page,
        info.outputPath(`observation-original-${scheme}.png`)
      );
    });
  }
});

async function setup(
  page: Page,
  status: InvestigationRequest['status'] = 'cannot_perform'
) {
  const row = { ...savedRequest(), status };
  const overview = {
    ...workOrder(),
    affected_component: 'Synthetic drive-end guard',
    affected_component_ref: 'SYN-GUARD-18',
    investigation: {
      rule: 'investigation.v1' as const,
      packet: null,
      explanation: null,
      findings: [],
      finding_history: 'not_applicable' as const,
      intervention: null,
      requests: { available: true, reason: '', items: [row, savedRequest(402)] }
    }
  };
  const requests = await mockExecutionWork(page, overview);
  await page.route('**/api/attachment/**', (route) =>
    route.fulfill({
      json: {
        count: 1,
        next: null,
        results: [
          {
            pk: 901,
            filename: 'Synthetic observation.png',
            model_type: 'workorder',
            model_id: 73
          }
        ]
      }
    })
  );
  return { overview, requests, row };
}
function saved(row: InvestigationRequest, intent: ObservationIntent) {
  row.version += 1;
  const artifact = {
    id: 501,
    request: row.id,
    kind: 'observation' as const,
    payload: intent.fields.payload,
    attachment: intent.fields.attachment_id ?? null,
    supersedes: null,
    recorded_by: 11,
    recorded_by_name: 'Synthetic technician',
    created_at: '2026-10-20T09:00:00Z'
  };
  row.artifacts = [...row.artifacts, artifact];
  return { request: row, artifact_id: artifact.id };
}
const draft = (page: Page, request = 401) =>
  page.getByTestId(`native-observation-${request}`);
async function open(page: Page, scheme: 'light' | 'dark' = 'light') {
  await openExecutionPanel(page, 'diagnose', scheme, false, 'editor');
  await draft(page)
    .getByRole('button', { name: 'Record observation manually', exact: true })
    .click();
}

async function captureObservation(page: Page, path: string) {
  await capturePanel(page, path);
}

for (const scheme of ['light', 'dark'] as const) {
  test(`secondary manual capture retains drafts and confirms exact request correlation (${scheme})`, async ({
    page
  }, info) => {
    const { row, requests } = await setup(page);
    const bodies: ObservationIntent[] = [];
    await page.route('**/investigation-requests/401/', async (route) => {
      const intent = route.request().postDataJSON();
      bodies.push(intent);
      await route.fulfill({ json: saved(row, intent) });
    });
    await open(page, scheme);
    await expect(draft(page)).toContainText(
      'This view has no confirmed save for this draft'
    );
    await expect(draft(page)).toContainText(
      'Synthetic drive-end guard · SYN-GUARD-18'
    );
    await draft(page)
      .getByLabel('Observed fact', { exact: true })
      .fill('Synthetic guard vibration observed; cause unconfirmed');
    await draft(page)
      .getByLabel('Observed at (optional, with time zone)', { exact: true })
      .fill('2026-10-20T08:30:00+03:00');
    await draft(page)
      .getByLabel('Conditions or instrument (optional)', { exact: true })
      .fill('Synthetic permitted observation window');
    await draft(page)
      .getByRole('combobox', {
        name: 'Existing Work attachment (optional)',
        exact: true
      })
      .click();
    await page
      .getByRole('option', {
        name: 'Synthetic observation.png · 901',
        exact: true
      })
      .click();
    await draft(page)
      .getByRole('button', { name: 'Hide manual observation', exact: true })
      .click();
    await page.getByRole('tab', { name: 'Work', exact: true }).click();
    await page.getByRole('tab', { name: 'Diagnose', exact: true }).click();
    await draft(page)
      .getByRole('button', { name: 'Record observation manually', exact: true })
      .click();
    await expect(
      draft(page).getByLabel('Observed fact', { exact: true })
    ).toHaveValue('Synthetic guard vibration observed; cause unconfirmed');
    await draft(page, 402)
      .getByRole('button', { name: 'Record observation manually', exact: true })
      .click();
    await expect(
      draft(page, 402).getByLabel('Observed fact', { exact: true })
    ).toHaveValue('');
    await draft(page, 402)
      .getByRole('button', { name: 'Hide manual observation', exact: true })
      .click();
    expect(requests.every((r) => ['GET', 'OPTIONS'].includes(r.method))).toBe(
      true
    );
    await expectNoPageOverflow(page);
    await captureObservation(
      page,
      info.outputPath(`observation-draft-${scheme}.png`)
    );
    await draft(page)
      .getByRole('button', { name: 'Save observation to request', exact: true })
      .click();
    await expect(draft(page)).toContainText('Observation saved');
    await expect(draft(page)).toContainText(
      'Correlated to Work 73 · Request 401 · Evidence 501'
    );
    await expect(page.getByTestId('investigation-request-401')).toContainText(
      'Saved observation · Work 73 · Request 401 · Evidence 501'
    );
    await expect(page.getByTestId('investigation-request-401')).toContainText(
      'Cannot perform — unresolved'
    );
    expect(bodies).toHaveLength(1);
    expect(bodies[0]).toMatchObject({
      operation: 'artifact',
      expected_version: 7,
      request_version: 3,
      fields: {
        kind: 'observation',
        attachment_id: 901,
        payload: {
          text: 'Synthetic guard vibration observed; cause unconfirmed',
          observed_at: '2026-10-20T08:30:00+03:00'
        }
      }
    });
    expect(requests.filter((r) => r.method === 'POST')).toHaveLength(1);
    await expectNoPageOverflow(page);
    await captureObservation(
      page,
      info.outputPath(`observation-saved-${scheme}.png`)
    );
  });
}

test('lost response retries the identical intent and keeps the retained draft', async ({
  page
}, info) => {
  const { row } = await setup(page);
  const bodies: ObservationIntent[] = [];
  let receipt: ReturnType<typeof saved>;
  await page.route('**/investigation-requests/401/', async (route) => {
    const intent = route.request().postDataJSON();
    bodies.push(intent);
    if (bodies.length === 1) {
      receipt = saved(row, intent);
      await route.abort('failed');
    } else await route.fulfill({ json: receipt });
  });
  await open(page, 'dark');
  await draft(page)
    .getByLabel('Observed fact', { exact: true })
    .fill('Retain this exact observed fact');
  await draft(page)
    .getByRole('button', { name: 'Save observation to request', exact: true })
    .click();
  await expect(draft(page)).toContainText('Save outcome unknown');
  await expect(draft(page)).toContainText(
    'This view has no confirmed save for this draft'
  );
  await expect(
    draft(page).getByLabel('Observed fact', { exact: true })
  ).toHaveJSProperty('readOnly', true);
  await page.getByRole('tab', { name: 'Work', exact: true }).click();
  await page.getByRole('tab', { name: 'Diagnose', exact: true }).click();
  await expect(
    draft(page).getByLabel('Observed fact', { exact: true })
  ).toHaveValue('Retain this exact observed fact');
  await captureObservation(
    page,
    info.outputPath('observation-uncertain-dark.png')
  );
  await draft(page)
    .getByRole('button', { name: 'Retry original save', exact: true })
    .click();
  await expect(draft(page)).toContainText('Observation saved');
  expect(bodies).toHaveLength(2);
  expect(bodies[1]).toEqual(bodies[0]);
  expect(row.artifacts).toHaveLength(1);
});

test('stale request retains the draft and requires explicit review before a new save', async ({
  page
}, info) => {
  const { row } = await setup(page);
  const bodies: ObservationIntent[] = [];
  await page.route('**/investigation-requests/401/', async (route) => {
    const intent = route.request().postDataJSON();
    bodies.push(intent);
    if (bodies.length === 1) {
      row.version = 4;
      row.purpose = 'Changed decision purpose from another technician';
      await route.fulfill({
        status: 409,
        json: { code: 'STALE_VERSION', detail: 'Request changed' }
      });
    } else await route.fulfill({ json: saved(row, intent) });
  });
  await open(page);
  await draft(page)
    .getByLabel('Observed fact', { exact: true })
    .fill('Draft stays on request 401');
  await draft(page)
    .getByRole('button', { name: 'Save observation to request', exact: true })
    .click();
  await expect(draft(page)).toContainText('This record changed');
  await expect(
    draft(page).getByRole('button', {
      name: 'Save observation to request',
      exact: true
    })
  ).toBeDisabled();
  await draft(page)
    .getByRole('button', { name: 'Review latest request', exact: true })
    .click();
  await expect(draft(page)).toContainText('Latest request loaded');
  await expect(page.getByTestId('investigation-request-401')).toContainText(
    'Changed decision purpose'
  );
  await expect(
    draft(page).getByLabel('Observed fact', { exact: true })
  ).toHaveValue('Draft stays on request 401');
  await captureObservation(
    page,
    info.outputPath('observation-stale-reviewed-light.png')
  );
  await draft(page)
    .getByRole('button', { name: 'Save observation to request', exact: true })
    .click();
  await expect(draft(page)).toContainText('Observation saved');
  expect(bodies[1].request_version).toBe(4);
  expect(bodies[1].idempotency_key).not.toBe(bodies[0].idempotency_key);
});

test('unconfirmed success is uncertain and a subsequent refusal never disproves the first save', async ({
  page
}, info) => {
  await setup(page);
  const bodies: ObservationIntent[] = [];
  await page.route('**/investigation-requests/401/', async (route) => {
    bodies.push(route.request().postDataJSON());
    await route.fulfill(
      bodies.length === 1
        ? { json: { status: 'ok' } }
        : { status: 403, json: { detail: 'Revoked' } }
    );
  });
  await open(page, 'dark');
  await draft(page)
    .getByLabel('Observed fact', { exact: true })
    .fill('Original uncertain observation');
  await draft(page)
    .getByRole('button', { name: 'Save observation to request', exact: true })
    .click();
  await expect(draft(page)).toContainText('Outcome unknown');
  await draft(page)
    .getByRole('button', { name: 'Retry original save', exact: true })
    .click();
  await expect(draft(page)).toContainText('Save outcome unknown');
  await expect(
    draft(page).getByText('Observation saved', { exact: true })
  ).toHaveCount(0);
  expect(bodies[1]).toEqual(bodies[0]);
  await expectNoPageOverflow(page);
  await captureObservation(
    page,
    info.outputPath('observation-refused-uncertain-dark.png')
  );
});

test('reader stays read-only and resolved requests cannot accept a manual observation', async ({
  page
}, info) => {
  const { requests, overview } = await setup(page, 'resolved');
  overview.affected_component = '';
  overview.affected_component_ref = '';
  await openExecutionPanel(page, 'diagnose');
  await expect(
    page.getByRole('button', {
      name: 'Record observation manually',
      exact: true
    })
  ).toHaveCount(0);
  await expect(
    page
      .getByRole('tabpanel', { name: 'Diagnose', exact: true })
      .locator('textarea')
  ).toHaveCount(0);
  await open(page);
  const resolved = page.getByTestId('investigation-request-401');
  await expect(resolved).toContainText('Recorded blocked reason');
  await expect(resolved).toContainText(
    'This earlier blocked reason is retained with the resolved request'
  );
  await expect(resolved).not.toContainText(
    'This decision remains unresolved until addressed'
  );
  await expect(draft(page)).toContainText('Component not supplied');
  await draft(page)
    .getByLabel('Observed fact', { exact: true })
    .fill('Cannot publish to a resolved request');
  await expect(
    draft(page).getByRole('button', {
      name: 'Save observation to request',
      exact: true
    })
  ).toBeDisabled();
  await expect(draft(page)).toContainText('This request is resolved');
  expect(requests.filter((r) => r.method === 'POST')).toHaveLength(0);
  await captureObservation(
    page,
    info.outputPath('observation-resolved-light.png')
  );
});

test('a changed machine cannot silently retarget a retained request draft', async ({
  page
}, info) => {
  const { overview, requests } = await setup(page);
  await open(page);
  await draft(page)
    .getByLabel('Observed fact', { exact: true })
    .fill('Original machine observation stays in context');
  overview.machine = 19;
  overview.machine_name = 'Synthetic different asset';
  await draft(page)
    .getByRole('button', { name: 'Review latest request', exact: true })
    .click();
  await expect(draft(page)).toContainText(
    'Machine or component context changed'
  );
  await expect(draft(page)).toContainText('Capture context: Synthetic pump');
  await expect(
    draft(page).getByLabel('Observed fact', { exact: true })
  ).toHaveValue('Original machine observation stays in context');
  await expect(
    draft(page).getByRole('button', {
      name: 'Save observation to request',
      exact: true
    })
  ).toBeDisabled();
  expect(requests.filter((row) => row.method === 'POST')).toHaveLength(0);
  await expectNoPageOverflow(page);
  await captureObservation(
    page,
    info.outputPath('observation-changed-asset-light.png')
  );
});

test('a failed current read retains the draft and can recover without a write', async ({
  page
}, info) => {
  const { overview, requests } = await setup(page);
  let failRead = false;
  await page.route('**/api/kanban/work-orders/73/overview/', (route) =>
    failRead
      ? route.fulfill({
          status: 503,
          json: { detail: 'Synthetic read outage' }
        })
      : route.fulfill({ json: overview })
  );
  await open(page, 'dark');
  await draft(page)
    .getByLabel('Observed fact', { exact: true })
    .fill('Retained during a current-read outage');
  failRead = true;
  await draft(page)
    .getByRole('button', { name: 'Review latest request', exact: true })
    .click();
  await expect(draft(page)).toContainText('Latest request could not be read');
  await expect(
    draft(page).getByLabel('Observed fact', { exact: true })
  ).toHaveValue('Retained during a current-read outage');
  await expect(
    draft(page).getByRole('button', {
      name: 'Save observation to request',
      exact: true
    })
  ).toBeDisabled();
  await captureObservation(
    page,
    info.outputPath('observation-read-outage-dark.png')
  );
  failRead = false;
  await draft(page)
    .getByRole('button', { name: 'Review latest request', exact: true })
    .click();
  await expect(draft(page)).toContainText('Latest request loaded');
  await expect(
    draft(page).getByLabel('Observed fact', { exact: true })
  ).toHaveValue('Retained during a current-read outage');
  await expect(
    draft(page).getByRole('button', {
      name: 'Save observation to request',
      exact: true
    })
  ).toBeEnabled();
  expect(requests.filter((row) => row.method === 'POST')).toHaveLength(0);
});

for (const status of [403, 404]) {
  test(`a refused current read hides the cached Work (${status})`, async ({
    page
  }) => {
    await setup(page);
    await open(page);
    await draft(page)
      .getByLabel('Observed fact', { exact: true })
      .fill('Private draft for originally authorized Work');
    await page.route('**/api/kanban/work-orders/73/overview/', (route) =>
      route.fulfill({ status, json: { detail: 'Synthetic scope refusal' } })
    );
    await draft(page)
      .getByRole('button', { name: 'Review latest request', exact: true })
      .click();
    await expect(
      page.getByText('Work order unavailable', { exact: true })
    ).toBeVisible();
    await expect(page.getByTestId('investigation-request-401')).toHaveCount(0);
    await expect(
      page.getByRole('textbox', { name: 'Observed fact', exact: true })
    ).toHaveCount(0);
  });
}
