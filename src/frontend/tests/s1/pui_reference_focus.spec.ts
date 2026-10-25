/** Real Equipment/Closeout -> existing drawer with mocked native providers. */
import { type Page, expect, test } from '@playwright/test';
import { openChat, prepareDrawerFixture } from '../pages/ai_drawer_harness';
import { sseBody } from '../pages/aichat_harness';
import { nativeBaseline } from '../s0/guards';
import { mockMachine } from '../s0/machineFixtures';
import { positionsFixture } from '../s2/equipmentFixtures';
import { closeoutWork } from '../s6/closeoutFixtures';
nativeBaseline();
const revision = 'a'.repeat(64);
async function focused(
  page: Page,
  options: {
    refuse?: boolean;
    mismatch?: boolean;
    capable?: boolean;
    beforeFocus?: () => Promise<unknown>;
  } = {}
) {
  await prepareDrawerFixture(page);
  await options.beforeFocus?.();
  const acks: any[] = [];
  const sends: any[] = [];
  await page.route('**/api/approvals/count/**', (r) =>
    r.fulfill({ json: { count: 0 } })
  );
  await page.route('**/api/aichat/proposals/**', (r) =>
    r.fulfill({ json: { results: [] } })
  );
  await page.route('https://cdn.svar.dev/**', (r) =>
    r.fulfill({ status: 404, body: '' })
  );
  await page.route(/\/api\/ai\/threads(?:\?|$)/, (r) =>
    r.fulfill({
      json: {
        threads: [],
        shared_threads: [],
        has_more: false,
        capabilities: {
          work_focus: true,
          component_evidence_focus: options.capable !== false
        }
      }
    })
  );
  await page.route('**/api/ai/threads/*/work-focus', async (r) => {
    const body = r.request().postDataJSON();
    acks.push(body);
    if (options.refuse)
      return r.fulfill({
        status: 400,
        json: { detail: 'work_focus_rejected' }
      });
    const thread = decodeURIComponent(
      new URL(r.request().url()).pathname.split('/').at(-2)!
    );
    return r.fulfill({
      json: {
        schema_version: 1,
        thread_id: thread,
        focus: {
          schema_version: 3,
          kind:
            body.work_order_id === null ? 'reference_position' : 'work_order',
          work_order_id: body.work_order_id,
          lifecycle_version: body.expected_version,
          machine_id: 18,
          ...(body.component
            ? {
                component: {
                  ...body.component,
                  ...(options.mismatch ? { position_id: 'motor' } : {})
                }
              }
            : {}),
          ...(body.evidence ? { evidence: body.evidence } : {})
        },
        receipt: 'synthetic-p02-receipt',
        expires_in: 300
      }
    });
  });
  await page.route('**/api/ai/chat/stream', async (r) => {
    const body = r.request().postDataJSON();
    sends.push(body);
    return r.fulfill({
      contentType: 'text/event-stream',
      body: sseBody([
        { type: 'RUN_STARTED', threadId: body.thread_id, runId: 'p02' },
        {
          type: 'TEXT_MESSAGE_START',
          threadId: body.thread_id,
          runId: 'p02',
          messageId: 'p02-answer',
          role: 'assistant'
        },
        {
          type: 'TEXT_MESSAGE_CONTENT',
          threadId: body.thread_id,
          runId: 'p02',
          messageId: 'p02-answer',
          delta: 'Synthetic focused answer; no physical state is inferred.'
        },
        {
          type: 'TEXT_MESSAGE_END',
          threadId: body.thread_id,
          runId: 'p02',
          messageId: 'p02-answer'
        },
        { type: 'RUN_FINISHED', threadId: body.thread_id, runId: 'p02' }
      ])
    });
  });
  return { acks, sends };
}
async function equipment(page: Page, scheme = 'light') {
  // The explicit model route also loads the reference renderer in remote Chromium.
  test.setTimeout(90_000);
  await mockMachine(page, { assetType: 'pump' });
  const positions = positionsFixture();
  await page.route('**/api/assets/machines/18/positions/', (r) =>
    r.fulfill({
      json: {
        ...positions,
        positions: positions.positions.map((p) => ({
          ...p,
          focus_revision: revision
        }))
      }
    })
  );
  await page.goto(
    `/playwright/s0-native.html?surface=machine-shell&sprint=s2&role=viewer&work_access=reader&scheme=${scheme}&entry=${encodeURIComponent('/machines/machine/18/equipment/?view=model')}`
  );
  // Position focus remains available through the native component list while
  // the optional reference renderer loads. Renderer checks live in S2.
  await expect(
    page.getByRole('button', { name: 'Ask about this position' })
  ).toBeEnabled({ timeout: 30_000 });
  await page.locator('[data-position="bearing"]').click();
  await page.getByRole('button', { name: 'Ask about this position' }).click();
  return page.getByRole('textbox', { name: 'Message', exact: true });
}
for (const scheme of ['light', 'dark'])
  test(`Equipment position acknowledged and exact return in ${scheme}`, async ({
    page
  }, info) => {
    const observed = await focused(page);
    const composer = await equipment(page, scheme);
    await expect(page.getByTestId('ai-chat-work-focus')).toContainText(
      'reference position'
    );
    await composer.fill('What is recorded for this reference position?');
    await page.getByTestId('send-ai-chat-message').click();
    await expect(
      page.getByText(
        'Synthetic focused answer; no physical state is inferred.',
        { exact: true }
      )
    ).toBeVisible({ timeout: 15_000 });
    expect(observed.acks).toEqual([
      {
        work_order_id: null,
        expected_version: null,
        component: {
          machine_id: 18,
          rule: 'reference_positions.v1',
          position_id: 'bearing',
          slot_code: null,
          revision
        }
      }
    ]);
    expect(observed.sends).toHaveLength(1);
    expect(observed.sends[0].work_focus_receipt).toBe('synthetic-p02-receipt');
    expect(observed.sends[0]).not.toHaveProperty('component');
    const chips = page.getByTestId('ai-chat-message-work-focus');
    await expect(chips).toHaveCount(2);
    await expect(chips.first().getByRole('link')).toHaveAttribute(
      'href',
      '/machines/machine/18/equipment/?position=bearing&view=model'
    );
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth
      )
    ).toBe(true);
    await page.screenshot({
      path: info.outputPath(`component-${scheme}.png`),
      fullPage: false
    });
    await chips.first().getByRole('link').click();
    await expect(page.locator('[data-selected-position]')).toHaveAttribute(
      'data-selected-position',
      'bearing'
    );
    if (info.project.name.includes('phone'))
      await expect(
        page.getByRole('textbox', { name: 'Message', exact: true })
      ).toBeHidden();
  });
for (const problem of ['refuse', 'mismatch', 'unsupported'] as const)
  test(`${problem} reference retains draft and sends nothing`, async ({
    page
  }, info) => {
    const observed = await focused(page, {
      refuse: problem === 'refuse',
      mismatch: problem === 'mismatch',
      capable: problem !== 'unsupported'
    });
    const composer = await equipment(page);
    await composer.fill('Keep my exact reference question.');
    await page.getByTestId('send-ai-chat-message').click();
    await expect(
      page.getByRole('alert').filter({ hasText: 'Reference not confirmed' })
    ).toBeVisible();
    await expect(composer).toHaveValue('Keep my exact reference question.');
    expect(observed.sends).toHaveLength(0);
    expect(observed.acks).toHaveLength(problem === 'unsupported' ? 0 : 1);
    await page.screenshot({
      path: info.outputPath(`${problem}.png`),
      fullPage: false
    });
    if (problem === 'unsupported') {
      await page
        .getByRole('button', { name: 'Remove reference position' })
        .click();
      await expect(page.getByTestId('ai-chat-work-focus')).toHaveCount(0);
      await expect(composer).toHaveValue('Keep my exact reference question.');
      await page.getByTestId('send-ai-chat-message').click();
      await expect(
        page.getByText(
          'Synthetic focused answer; no physical state is inferred.',
          { exact: true }
        )
      ).toBeVisible({ timeout: 15_000 });
      expect(observed.sends).toHaveLength(1);
      expect(observed.sends[0]).not.toHaveProperty('work_focus_receipt');
    }
  });
for (const scheme of ['light', 'dark'])
  test(`recorded reading launches exact evidence in ${scheme}`, async ({
    page
  }, info) => {
    const observed = await focused(page, {
      beforeFocus: () => closeoutWork(page)
    });
    await page.route('**/api/tasks/work-orders/73/closeout/readings/', (r) =>
      r.fulfill({
        json: [
          {
            id: 51,
            label: 'Zero temperature',
            raw_text: '0',
            value: '0',
            unit: 'degC',
            required: false,
            verification_state: 'pending',
            warnings: [],
            focus_revision: revision
          }
        ]
      })
    );
    await page.goto(
      `/playwright/s0-native.html?surface=maintenance-shell&sprint=s6&role=viewer&work_access=reader&scheme=${scheme}&entry=${encodeURIComponent('/maintenance/work-orders/73/?work_panel=closeout')}`
    );
    await page.getByRole('button', { name: 'Ask about this reading' }).click();
    const composer = page.getByRole('textbox', {
      name: 'Message',
      exact: true
    });
    await composer.fill('Which qualifiers were recorded for this reading?');
    await page.getByTestId('send-ai-chat-message').click();
    await expect(
      page.getByText(
        'Synthetic focused answer; no physical state is inferred.',
        { exact: true }
      )
    ).toBeVisible();
    expect(observed.acks).toEqual([
      {
        work_order_id: 73,
        expected_version: 7,
        evidence: { kind: 'closeout_reading', id: 51, revision }
      }
    ]);
    expect(observed.sends).toHaveLength(1);
    expect(observed.sends[0].work_focus_receipt).toBe('synthetic-p02-receipt');
    expect(observed.sends[0]).not.toHaveProperty('evidence');
    const chips = page.getByTestId('ai-chat-message-work-focus');
    await expect(chips).toHaveCount(2);
    await expect(chips.first()).toContainText('Closeout reading #51');
    await expect(chips.first().getByRole('link')).toHaveAttribute(
      'href',
      '/maintenance/work-orders/73?work_panel=closeout'
    );
    await page.screenshot({
      path: info.outputPath(`evidence-${scheme}.png`),
      fullPage: false
    });
    await chips.first().getByRole('link').click();
    if (info.project.name.includes('phone'))
      await expect(composer).toBeHidden();
    else await page.getByTestId('close-ai-chat').click();
    await page
      .getByRole('button', { name: 'Ask about this reading' })
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: info.outputPath(`reading-source-${scheme}.png`),
      fullPage: false
    });
    await page
      .getByRole('tab', { name: 'Verify & Return', exact: true })
      .click();
    const service = page
      .getByRole('tabpanel', { name: 'Verify & Return', exact: true })
      .getByTestId('service-restoration');
    await expect(
      service.getByRole('button', {
        name: /Information: Work completion, test results and telemetry are separate from test acceptance, operating permission, service capacity and remaining obligations/
      })
    ).toBeVisible();
    const gaps = page
      .getByRole('tabpanel', { name: 'Verify & Return', exact: true })
      .getByTestId('not-supplied');
    await gaps.getByRole('button').click();
    await expect(gaps).toContainText(
      'Test acceptance, operating permission, observed return, required service and standby capacity: authoritative records are not supplied by this server.'
    );
    await service.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: info.outputPath(`authority-absent-${scheme}.png`),
      fullPage: false
    });
  });

for (const scheme of ['light', 'dark'] as const)
  test(`saved combined reference states remain explicit in ${scheme}`, async ({
    page
  }, info) => {
    await page.emulateMedia({ colorScheme: scheme });
    const component = {
      machine_id: 18,
      rule: 'reference_positions.v1',
      position_id: 'bearing',
      slot_code: null,
      revision
    };
    await prepareDrawerFixture(page, {
      voice: false,
      foundation: {
        threadDetailMessages: ['current', 'changed', 'unavailable'].map(
          (state, index) => ({
            id: `reference-${state}`,
            role: 'assistant',
            content: `Reference answer ${state}`,
            timestamp: `2026-10-19T00:0${index + 1}:00Z`,
            work_focus: {
              schema_version: 3,
              kind: 'work_order',
              work_order_id: 73,
              lifecycle_version: 7,
              machine_id: 18,
              request_id: 5,
              request_version: 2,
              component,
              evidence: { kind: 'closeout_reading', id: 51 + index, revision }
            },
            work_focus_state: state,
            work_focus_reference_states: {
              component: 'current',
              evidence: state
            }
          })
        )
      }
    });
    await page.goto('/playwright/aichat-drawer.html?actor=reader');
    await openChat(page);
    const chips = page.getByTestId('ai-chat-message-work-focus');
    await expect(chips).toHaveCount(3);
    for (const chip of await chips.all()) {
      await expect(chip).toContainText('Request 5');
      await expect(chip).toContainText('Motor drive-end bearing');
      await expect(chip).toContainText('Reference position: Current');
    }
    await expect(chips.nth(0).getByRole('link')).toHaveAttribute(
      'href',
      '/maintenance/work-orders/73?work_panel=diagnose&investigation_request=5'
    );
    await expect(chips.nth(1)).toContainText(
      'Evidence: Changed since this answer'
    );
    await expect(chips.nth(2)).toContainText(
      'Evidence: No longer available to you'
    );
    await expect(chips.nth(2).getByRole('link')).toHaveCount(0);
    await chips.nth(1).scrollIntoViewIfNeeded();
    await page.screenshot({
      path: info.outputPath(`saved-reference-states-${scheme}.png`)
    });
  });

for (const scheme of ['light', 'dark'] as const)
  test(`readable Work keeps its link when reference sources disappear in ${scheme}`, async ({
    page
  }, info) => {
    await page.emulateMedia({ colorScheme: scheme });
    await prepareDrawerFixture(page, {
      voice: false,
      foundation: {
        threadDetailMessages: [
          {
            id: 'missing-reference-answer',
            role: 'assistant',
            content: 'Saved answer with unavailable child sources.',
            timestamp: '2026-10-19T00:01:00Z',
            work_focus: {
              schema_version: 3,
              kind: 'work_order',
              work_order_id: 73,
              lifecycle_version: 7,
              machine_id: 18,
              request_id: 5,
              request_version: 2,
              component: {
                machine_id: 18,
                rule: 'reference_positions.v1',
                position_id: 'bearing',
                slot_code: 'bearing:1',
                revision
              },
              evidence: { kind: 'closeout_reading', id: 51, revision }
            },
            work_focus_state: 'current',
            work_focus_reference_states: {
              component: 'unavailable',
              evidence: 'unavailable'
            }
          }
        ]
      }
    });
    await page.goto('/playwright/aichat-drawer.html?actor=reader');
    await openChat(page);
    const chip = page.getByTestId('ai-chat-message-work-focus');
    await expect(chip).toHaveAttribute('data-work-focus-state', 'current');
    await expect(chip.getByRole('link')).toHaveAttribute(
      'href',
      '/maintenance/work-orders/73?work_panel=diagnose&investigation_request=5'
    );
    await expect(chip).toContainText(
      'Reference position: No longer available to you'
    );
    await expect(chip).toContainText('Evidence: No longer available to you');
    await expect(
      chip.getByText('No longer available to you', { exact: true })
    ).toHaveCount(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth
      )
    ).toBe(true);
    await page.screenshot({
      path: info.outputPath(`readable-work-missing-references-${scheme}.png`)
    });
  });

test('a component-focused question keeps its uploaded file and signed focus', async ({
  page
}) => {
  const observed = await focused(page);
  const composer = await equipment(page);
  await page.route('**/api/ai/upload', (route) => {
    const threadId = route
      .request()
      .postData()
      ?.match(/name="thread_id"\r\n\r\n([^\r]+)/)?.[1];
    expect(threadId).toBeTruthy();
    return route.fulfill({
      json: {
        file_id: 'synthetic-thread/report.pdf',
        filename: 'report.pdf',
        extension: '.pdf',
        size: 42,
        thread_id: threadId
      }
    });
  });
  await page.locator('input[type=file]').setInputFiles({
    name: 'report.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from('%PDF-1.4 synthetic reference upload')
  });
  await expect(
    page.getByTestId('remove-ai-chat-attachment-synthetic-thread/report.pdf')
  ).toBeVisible();
  await composer.fill('Read this file about the selected reference position.');
  await page.getByTestId('send-ai-chat-message').click();
  await expect.poll(() => observed.sends.length).toBe(1);
  expect(observed.acks).toHaveLength(1);
  expect(observed.sends[0].file_ids).toEqual(['synthetic-thread/report.pdf']);
  expect(observed.sends[0].work_focus_receipt).toBe('synthetic-p02-receipt');
});
