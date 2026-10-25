/** Read-only terminal rich rendering on localhost; auth and APIs are mocked. */
import { type Page, expect, test } from '@playwright/test';
import { loadDrawerFixture, prepareDrawerFixture } from './ai_drawer_harness';
import { threadId } from './aichat_harness';

const presentation = (
  program = 'root = AnswerLayout([AnswerSummary(),AssetSummary("r0"),TrendPreview("r1")])'
) => ({
  schema_version: 1,
  format: 'openui-lang',
  library: 'aimms-chat@1',
  program,
  references: [
    { ref_id: 'r0', kind: 'entity', source_key: 'entity:assetmachine:7' },
    { ref_id: 'r1', kind: 'trend_dataset', source_key: 'timeline' }
  ]
});
const message = (overrides: Record<string, unknown> = {}) => ({
  id: 'rich-answer',
  role: 'assistant',
  timestamp: '2026-10-24T00:00:00Z',
  content:
    'Recorded inspection answer. Check the cited source before taking action.',
  entities: [{ model: 'assetmachine', pk: 7, label: 'Recorded Pump A' }],
  presentation: presentation(),
  presentation_data: [
    {
      source_key: 'timeline',
      kind: 'trend_dataset',
      source: 'work_orders',
      source_revision: 'snapshot-1',
      unit: 'records',
      timezone: 'UTC',
      as_of: '2026-10-24T00:00:00Z',
      samples: [
        { observed_at: '2026-01-01', value: 4, quality: 'good' },
        { observed_at: '2026-02-01', value: null, quality: 'missing' },
        { observed_at: '2026-04-01', value: 7, quality: 'uncertain' },
        { observed_at: '2026-07-01', value: 9, quality: 'good' }
      ]
    }
  ],
  ...overrides
});
async function saved(page: Page, data: unknown) {
  await prepareDrawerFixture(page, {
    voice: false,
    foundation: { threadDetailMessages: [data] }
  });
  await loadDrawerFixture(page);
  await page.getByRole('tab', { name: 'History', exact: true }).click();
  await page
    .getByTestId('ai-chat-history-row')
    .filter({ hasText: 'Server conversation' })
    .click();
  await expect(
    page.getByRole('article', { name: 'Assistant response' })
  ).toContainText('Recorded inspection answer.');
}
test('G01/G05: reload preserves the historical snapshot, gaps and accessible values without data fetches', async ({
  page
}, info) => {
  const requests: string[] = [];
  const external: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (!['localhost', '127.0.0.1'].includes(url.hostname))
      external.push(url.origin);
    if (/\/api\/.*(?:trend|metrics|telemetry)/.test(url.pathname))
      requests.push(url.pathname);
  });
  await saved(page, message());
  await expect(page.getByTestId('openui-answer')).toBeVisible();
  await expect(page.getByTestId('presentation-trend')).toContainText(
    'snapshot-1'
  );
  await expect(
    page.getByRole('cell', { name: 'Unavailable', exact: true })
  ).toHaveCount(1);
  await expect(
    page.getByRole('cell', { name: 'Uncertain', exact: true })
  ).toHaveCount(1);
  const path = page
    .getByTestId('presentation-trend')
    .locator('svg path')
    .nth(1);
  expect((await path.getAttribute('d'))?.match(/M/g)).toHaveLength(2);
  await page.reload();
  await page.getByTestId('open-ai-chat').click();
  await expect(page.getByTestId('presentation-trend')).toContainText(
    'snapshot-1'
  );
  expect(requests).toEqual([]);
  expect(external).toEqual([]);
  await expect(page.locator('[data-openui-devtools]')).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('rich-answer-desktop.png') });
});
for (const [name, override] of Object.entries({
  unsupported: { presentation: { ...presentation(), library: 'aimms-chat@2' } },
  malformed: {
    presentation: presentation(
      'root = AnswerLayout([AnswerSummary(), Unknown()])'
    )
  },
  query: {
    presentation: presentation(
      'root = AnswerLayout([AnswerSummary(), Query("private")])'
    )
  },
  incomplete: {
    presentation: presentation('root = AnswerLayout([AnswerSummary(),')
  },
  oversized: { presentation: presentation('机'.repeat(22000)) },
  absent: { presentation: undefined }
})) {
  test(`G02/G10/G11: ${name} metadata retains the full canonical fallback`, async ({
    page
  }) => {
    await saved(page, message(override));
    await expect(page.getByTestId('openui-answer')).toHaveCount(0);
    await expect(
      page.getByRole('article', { name: 'Assistant response' })
    ).toContainText('Check the cited source before taking action.');
    await expect(page.getByTestId('entity-chip-assetmachine:7')).toBeVisible();
  });
}
test('G04: missing references stay unavailable and never create a URL or invented zero', async ({
  page
}) => {
  const value = presentation();
  value.references[0].source_key = 'entity:assetmachine:999';
  value.references[1].source_key = 'revoked';
  await saved(page, message({ presentation: value }));
  await expect(page.getByTestId('openui-answer')).toBeVisible();
  await expect(
    page.getByText('Referenced data is unavailable.', { exact: true })
  ).toHaveCount(2);
  await expect(page.getByTestId('presentation-trend')).toHaveCount(0);
  await expect(page.getByTestId('entity-chip-assetmachine:999')).toHaveCount(0);
});
test('G09: copying a rich answer uses canonical prose without program markers', async ({
  page,
  context
}) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await saved(page, message());
  await expect(page.getByTestId('openui-answer')).toBeVisible();
  await page.getByTestId('copy-ai-chat-message').click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
    message().content
  );
});

for (const wire of ['legacy', 'agui'] as const) {
  test(`G03: ${wire} attaches the terminal presentation to its live answer`, async ({
    page
  }) => {
    await prepareDrawerFixture(page, {
      voice: false,
      foundation: {
        capabilities: { agui: wire === 'agui' },
        threadDetailMessages: [
          message({ presentation: undefined, presentation_data: undefined })
        ]
      }
    });
    const value = message();
    const events: Record<string, unknown>[] = [
      { type: 'RUN_STARTED', threadId, runId: 'rich-live' },
      {
        type: 'TEXT_MESSAGE_START',
        messageId: 'live-answer',
        role: 'assistant'
      },
      {
        type: 'TEXT_MESSAGE_CONTENT',
        messageId: 'live-answer',
        delta: 'A live recorded answer.'
      },
      ...(wire === 'agui'
        ? [
            {
              type: 'CUSTOM',
              name: 'aimms.entities',
              value: { entities: value.entities }
            },
            {
              type: 'CUSTOM',
              name: 'aimms.presentation',
              value: {
                presentation: value.presentation,
                presentation_data: value.presentation_data
              }
            }
          ]
        : [
            {
              type: 'STATE_DELTA',
              threadId,
              kind: 'entity_manifest',
              entities: value.entities
            },
            {
              type: 'STATE_DELTA',
              threadId,
              kind: 'presentation',
              presentation: value.presentation,
              presentation_data: value.presentation_data
            }
          ]),
      { type: 'TEXT_MESSAGE_END', messageId: 'live-answer' },
      { type: 'RUN_FINISHED', threadId, runId: 'rich-live' }
    ];
    let posts = 0;
    await page.route(
      wire === 'agui' ? '**/api/ai/agui' : '**/api/ai/chat/stream',
      async (route) => {
        posts++;
        await route.fulfill({
          contentType: 'text/event-stream',
          body: events
            .map((event) => `data: ${JSON.stringify(event)}\n\n`)
            .join('')
        });
      }
    );
    await loadDrawerFixture(page);
    await page.getByRole('tab', { name: 'History', exact: true }).click();
    await page
      .getByTestId('ai-chat-history-row')
      .filter({ hasText: 'Server conversation' })
      .click();
    await page
      .getByRole('textbox', { name: 'Message', exact: true })
      .fill('Inspect the recorded trend.');
    await page.getByTestId('send-ai-chat-message').click();
    await expect(page.getByTestId('openui-answer')).toBeVisible();
    await expect(page.getByTestId('openui-answer')).toContainText(
      'A live recorded answer.'
    );
    await expect(page.getByTestId('presentation-trend')).toContainText(
      'snapshot-1'
    );
    expect(posts).toBe(1);
    await expect(page.getByTestId('cancel-ai-chat-turn')).toHaveCount(0);
  });
}

test('G02: a renderer chunk load failure preserves the canonical answer and facts', async ({
  page
}) => {
  await page.route(
    '**/src/components/aichat/genui/OpenUIMessage.tsx*',
    (route) => route.abort('failed')
  );
  await saved(page, message());
  await expect(page.getByTestId('openui-answer')).toHaveCount(0);
  await expect(page.getByTestId('entity-chip-assetmachine:7')).toBeVisible();
  await expect(page.getByTestId('copy-ai-chat-message')).toBeVisible();
});
test('G02: an actual component throw returns the full canonical fallback', async ({
  page
}) => {
  let injected = false;
  await page.route(
    '**/src/components/aichat/genui/OpenUIMessage.tsx*',
    async (route) => {
      const response = await route.fetch();
      const body = await response.text();
      const patched = body.replace(
        'function Summary() {',
        'function Summary() { throw new Error("controlled-render-failure");'
      );
      injected = patched !== body;
      await route.fulfill({ response, body: patched });
    }
  );
  await saved(page, message());
  await expect.poll(() => injected).toBe(true);
  await expect(page.getByTestId('openui-answer')).toHaveCount(0);
  await expect(page.getByTestId('entity-chip-assetmachine:7')).toBeVisible();
  await expect(page.getByTestId('copy-ai-chat-message')).toBeVisible();
});
