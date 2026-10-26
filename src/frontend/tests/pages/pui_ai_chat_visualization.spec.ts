/** Real renderer on localhost; API/auth fixtures. Optional actual ORM-produced DTOs. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { type Page, expect, test } from '@playwright/test';
import { loadDrawerFixture, prepareDrawerFixture } from './ai_drawer_harness';
import { threadId } from './aichat_harness';

const runtimeErrors = new WeakMap<Page, string[]>();
test.beforeEach(async ({ page }) => {
  const errors: string[] = [];
  runtimeErrors.set(page, errors);
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (entry) => {
    if (entry.type() === 'error') errors.push(entry.text());
  });
});
test.afterEach(async ({ page }, info) => {
  const errors = runtimeErrors.get(page) ?? [];
  if (errors.length)
    await info.attach('browser-errors', {
      body: JSON.stringify(errors),
      contentType: 'application/json'
    });
});

function data(type: 'line' | 'bar' | 'pie') {
  return {
    kind: 'chart_dataset',
    source_key: 'provided:series',
    origin: 'user_supplied',
    chart_type: type,
    title: 'Synthetic test data',
    unit: 'test count',
    x_label: 'Month',
    y_label: 'Test count',
    source: 'user_supplied',
    source_revision: 'test-turn',
    as_of: '2026-10-25T12:00:00Z',
    scope_label: 'Values supplied in this message',
    definition: 'Synthetic values, not operational records.',
    population_count: null,
    complete_population: null,
    points:
      type === 'pie'
        ? [
            { label: 'Planned', value: 6 },
            { label: 'Corrective', value: 3 },
            { label: 'Inspection', value: 1 }
          ]
        : [
            { label: 'Jan', value: 4 },
            { label: 'Feb', value: 0 },
            { label: 'Mar', value: 7 },
            { label: 'Apr', value: 9 }
          ]
  };
}
function message(type: 'line' | 'bar' | 'pie') {
  return {
    id: 'chart-answer',
    role: 'assistant',
    timestamp: '2026-10-25T12:00:00Z',
    content: 'Synthetic chart values supplied in this message.',
    presentation: {
      schema_version: 1,
      format: 'openui-lang',
      library: 'aimms-chat@2',
      program: 'root = AnswerLayout([AnswerSummary(),ChartSnapshot("r0")])',
      references: [
        { ref_id: 'r0', kind: 'chart_dataset', source_key: 'provided:series' }
      ]
    },
    presentation_data: [data(type)]
  };
}
async function saved(page: Page, value: unknown) {
  await prepareDrawerFixture(page, {
    voice: false,
    foundation: { threadDetailMessages: [value] }
  });
  await loadDrawerFixture(page);
  await page.getByRole('tab', { name: 'History', exact: true }).click();
  await page
    .getByTestId('ai-chat-history-row')
    .filter({ hasText: 'Server conversation' })
    .click();
}
for (const width of [390, 1440])
  for (const theme of ['light', 'dark'] as const)
    for (const type of ['line', 'bar', 'pie'] as const) {
      test(`${type} graphic, values and provenance at ${width}px ${theme}`, async ({
        page
      }, info) => {
        await page.setViewportSize({ width, height: 900 });
        await page.emulateMedia({ colorScheme: theme });
        await saved(page, message(type));
        const chart = page.getByTestId('presentation-chart');
        await expect(chart.locator('svg[role="img"]')).toBeVisible();
        await expect(chart).toContainText('Synthetic / user-provided data');
        await expect(chart.getByRole('table')).toBeVisible();
        if (type === 'pie') {
          await expect(chart.locator('svg path')).toHaveCount(3);
          for (const value of ['60%', '30%', '10%'])
            await expect(
              chart.getByRole('cell', { name: value, exact: true })
            ).toBeVisible();
        } else {
          await expect(
            chart.getByRole('cell', { name: '0', exact: true })
          ).toBeVisible();
          if (type === 'line')
            await expect(chart.locator('svg circle')).toHaveCount(4);
          else await expect(chart.locator('svg rect')).toHaveCount(4);
        }
        expect(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= window.innerWidth
          )
        ).toBe(true);
        await chart.getByRole('region', { name: 'Chart values' }).focus();
        await page.screenshot({
          path: info.outputPath(`${type}-${width}-${theme}.png`)
        });
      });
    }
test('history retains the same graphic and values after reload without data queries', async ({
  page
}) => {
  const fetches: string[] = [];
  page.on('request', (request) => {
    if (
      /\/api\/.*(?:trend|metrics|telemetry)/.test(
        new URL(request.url()).pathname
      )
    )
      fetches.push(request.url());
  });
  await saved(page, message('line'));
  const before = await page
    .getByTestId('presentation-chart')
    .locator('svg')
    .innerHTML();
  await page.reload();
  await page.getByTestId('open-ai-chat').click();
  await expect(page.getByTestId('presentation-chart')).toBeVisible();
  expect(
    await page.getByTestId('presentation-chart').locator('svg').innerHTML()
  ).toBe(before);
  expect(fetches).toEqual([]);
});
for (const wire of ['legacy', 'agui'] as const) {
  test(`${wire} streams canonical text then attaches exactly one chart`, async ({
    page
  }) => {
    const value = message('pie');
    await prepareDrawerFixture(page, {
      voice: false,
      foundation: {
        capabilities: { agui: wire === 'agui' },
        threadDetailMessages: [
          { ...value, presentation: undefined, presentation_data: undefined }
        ]
      }
    });
    const attachment = {
      presentation: value.presentation,
      presentation_data: value.presentation_data
    };
    const events = [
      { type: 'RUN_STARTED', threadId, runId: 'chart-live' },
      {
        type: 'TEXT_MESSAGE_START',
        messageId: 'chart-live-message',
        role: 'assistant'
      },
      {
        type: 'TEXT_MESSAGE_CONTENT',
        messageId: 'chart-live-message',
        delta: value.content
      },
      wire === 'agui'
        ? { type: 'CUSTOM', name: 'aimms.presentation', value: attachment }
        : {
            type: 'STATE_DELTA',
            threadId,
            kind: 'presentation',
            ...attachment
          },
      { type: 'TEXT_MESSAGE_END', messageId: 'chart-live-message' },
      { type: 'RUN_FINISHED', threadId, runId: 'chart-live' }
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
      .fill('Display the supplied pie chart.');
    await page.getByTestId('send-ai-chat-message').click();
    await expect(page.getByTestId('presentation-chart')).toHaveCount(1);
    await expect(
      page.getByTestId('presentation-chart').locator('svg path')
    ).toHaveCount(3);
    expect(posts).toBe(1);
    await expect(page.getByTestId('openui-answer')).not.toContainText('root =');
  });
}
const artifactDir = process.env.AIMMS_TEST_PRESENTATION_ARTIFACT_DIR;
for (const name of [
  'followup-trend-model',
  'followup-trend-recovered',
  'followup-status-model',
  'followup-status-recovered'
]) {
  for (const width of [320, 390, 1440])
    for (const theme of ['light', 'dark'] as const)
      test(`${name} at ${width}px ${theme}`, async ({ page }, info) => {
        test.skip(
          !artifactDir,
          'Run the native scoped follow-up regressions first.'
        );
        const canonical = JSON.parse(
          readFileSync(join(artifactDir!, `${name}.json`), 'utf8')
        );
        await page.setViewportSize({
          width,
          height: width === 320 ? 480 : 900
        });
        await page.emulateMedia({ colorScheme: theme });
        await saved(page, {
          id: name,
          role: 'assistant',
          timestamp: '2026-10-25T12:00:00Z',
          content: canonical.message,
          ...canonical
        });
        const chart = page.getByTestId(
          name.includes('trend') ? 'presentation-trend' : 'presentation-chart'
        );
        await expect(chart.locator('svg[role="img"]')).toBeVisible();
        await expect(chart.getByRole('table')).toBeVisible();
        if (name.includes('status')) {
          await expect(
            chart.getByRole('cell', { name: '1', exact: true })
          ).toBeVisible();
          await expect(
            chart.getByRole('cell', { name: '3', exact: true })
          ).toBeVisible();
          await expect(
            chart.getByRole('cell', { name: '0', exact: true })
          ).toBeVisible();
        }
        expect(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= window.innerWidth
          )
        ).toBe(true);
        await expect(page.getByTestId('send-ai-chat-message')).toBeInViewport();
        await chart.locator('svg[role="img"]').scrollIntoViewIfNeeded();
        await expect(chart.locator('svg[role="img"]')).toBeInViewport();
        await page.screenshot({
          path: info.outputPath(`${name}-${width}-${theme}.png`)
        });
        const lastRow = chart.getByRole('row').last();
        await lastRow.scrollIntoViewIfNeeded();
        await expect(lastRow).toBeInViewport();
      });
}
for (const index of [0, 1, 2, 3, 4])
  test(`clean native identity ${index}`, async ({ page }, info) => {
    test.skip(
      !artifactDir,
      'Run the native identity follow-up regressions first.'
    );
    const canonical = JSON.parse(
      readFileSync(
        join(artifactDir!, `followup-identity-${index}.json`),
        'utf8'
      )
    );
    await saved(page, {
      id: `identity-${index}`,
      role: 'assistant',
      timestamp: '2026-10-25T12:00:00Z',
      content: canonical.message,
      ...canonical
    });
    await expect(
      page.getByRole('link', { name: canonical.entities[0].label, exact: true })
    ).toBeVisible();
    await expect(page.getByTestId('openui-answer')).not.toContainText(
      '[UNTRUSTED-CONTENT-'
    );
    await expect(page.getByTestId('openui-answer')).toContainText(
      'Turbo Generation 5'
    );
    await page.screenshot({ path: info.outputPath(`identity-${index}.png`) });
  });
test('monthly range limit states the supported window', async ({ page }) => {
  test.skip(!artifactDir, 'Run the native range-limit follow-up first.');
  const canonical = JSON.parse(
    readFileSync(join(artifactDir!, 'followup-range-limit.json'), 'utf8')
  );
  await saved(page, {
    id: 'range-limit',
    role: 'assistant',
    timestamp: '2026-10-25T12:00:00Z',
    content: canonical.message,
    ...canonical
  });
  await expect(
    page.getByText(/Monthly charts support 1-36 calendar months/)
  ).toBeVisible();
  await expect(page.getByTestId('presentation-chart')).toHaveCount(0);
  await expect(page.getByTestId('presentation-trend')).toHaveCount(0);
});
for (const name of ['recorded-status', 'recorded-trend', 'provided-pie']) {
  test(`renders actual backend-produced ${name} snapshot`, async ({ page }) => {
    test.skip(
      !artifactDir,
      'Run the real ORM integration with AIMMS_TEST_PRESENTATION_ARTIFACT_DIR first.'
    );
    const canonical = JSON.parse(
      readFileSync(join(artifactDir!, `${name}.json`), 'utf8')
    );
    await saved(page, {
      id: name,
      role: 'assistant',
      timestamp: '2026-10-25T12:00:00Z',
      content: canonical.message,
      ...canonical
    });
    const chart = page.getByTestId(
      name === 'recorded-trend' ? 'presentation-trend' : 'presentation-chart'
    );
    await expect(chart.locator('svg[role="img"]')).toBeVisible();
    await expect(chart.getByRole('table')).toBeVisible();
    if (name === 'recorded-status')
      for (const count of ['29', '7', '3'])
        await expect(
          chart.getByRole('cell', { name: count, exact: true })
        ).toBeVisible();
  });
}
