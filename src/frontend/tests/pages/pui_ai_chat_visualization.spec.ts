/** Real renderer on localhost; API/auth fixtures. Optional actual ORM-produced DTOs. */
import { readFileSync, writeFileSync } from 'node:fs';
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

test.describe('native color-menu and mobile layout transitions', () => {
  test.use({ reducedMotion: 'no-preference' });
  for (const type of ['line', 'bar'] as const)
    for (const theme of ['light', 'dark'] as const) {
      test(`${type} remains usable across native menu closure and mobile resize in ${theme}`, async ({
        page
      }, info) => {
        await page.setViewportSize({ width: 1440, height: 900 });
        await page.emulateMedia({
          colorScheme: theme === 'light' ? 'dark' : 'light'
        });
        await prepareDrawerFixture(page, {
          voice: false,
          foundation: { threadDetailMessages: [message(type)] }
        });
        await page.goto('/playwright/aichat-drawer.html?native-menu=1');
        await page
          .getByRole('button', { name: 'Drawer fixture', exact: true })
          .click();
        await page
          .getByRole('menuitem', { name: 'Change Color Mode', exact: true })
          .click();
        await expect(page.getByRole('menu')).toBeHidden();
        await expect(page.locator('html')).toHaveAttribute(
          'data-mantine-color-scheme',
          theme
        );
        await page.getByTestId('open-ai-chat').click();
        await page.getByRole('tab', { name: 'History', exact: true }).click();
        await page
          .getByTestId('ai-chat-history-row')
          .filter({ hasText: 'Server conversation' })
          .click();
        const chart = page.getByTestId('presentation-chart');
        await expect(chart).toBeVisible();
        for (const size of [
          { width: 390, height: 844 },
          { width: 320, height: 480 }
        ]) {
          await page.setViewportSize(size);
          const samples = await page.evaluate(async () => {
            const observations = [];
            for (let frame = 0; frame < 30; frame++) {
              await new Promise<void>((resolve) =>
                requestAnimationFrame(() => resolve())
              );
              const drawer = document
                .querySelector('[data-testid="ai-chat-drawer"]')!
                .getBoundingClientRect();
              const svg = document
                .querySelector('[data-testid="presentation-chart"] svg')!
                .getBoundingClientRect();
              const send = document
                .querySelector('[data-testid="send-ai-chat-message"]')!
                .getBoundingClientRect();
              observations.push({
                overflowElements: [...document.querySelectorAll('body *')]
                  .filter((el) => {
                    const rect = el.getBoundingClientRect();
                    return (
                      rect.width > 0 &&
                      rect.right > innerWidth + 1 &&
                      getComputedStyle(el).position !== 'fixed'
                    );
                  })
                  .slice(0, 10)
                  .map((el) => ({
                    tag: el.tagName,
                    className: el.className,
                    right: el.getBoundingClientRect().right
                  })),
                documentWidth: document.documentElement.scrollWidth,
                viewportWidth: innerWidth,
                viewportHeight: innerHeight,
                drawer: {
                  left: drawer.left,
                  right: drawer.right,
                  bottom: drawer.bottom
                },
                svg: { left: svg.left, right: svg.right },
                send: { right: send.right, bottom: send.bottom },
                chartFits:
                  svg.left >= drawer.left - 1 && svg.right <= innerWidth + 1,
                composerFits:
                  send.right <= innerWidth + 1 && send.bottom <= innerHeight + 1
              });
            }
            return observations;
          });
          await info.attach(`${size.width}-transition-geometry`, {
            body: JSON.stringify(samples),
            contentType: 'application/json'
          });
          writeFileSync(
            info.outputPath(`${size.width}-transition-geometry.json`),
            JSON.stringify(samples, null, 2)
          );
          expect(
            samples.every((sample) => sample.chartFits && sample.composerFits),
            JSON.stringify(
              samples.filter(
                (sample) => !sample.chartFits || !sample.composerFits
              )
            )
          ).toBe(true);
          await expect
            .poll(() =>
              page.evaluate(
                () => document.documentElement.scrollWidth <= innerWidth
              )
            )
            .toBe(true);
          await chart.locator('tbody tr').last().scrollIntoViewIfNeeded();
          await expect(chart.locator('tbody tr').last()).toBeInViewport();
          await page.screenshot({
            path: info.outputPath(`${type}-${theme}-${size.width}-settled.png`)
          });
        }
      });
    }
});

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
if (process.env.AIMMS_REQUIRE_NATIVE_DTOS === '1') {
  if (!artifactDir)
    throw new Error('Native DTO directory is required for qualification.');
  for (const name of [
    'followup-trend-model',
    'followup-trend-recovered',
    'followup-status-model',
    'followup-status-recovered',
    'followup-range-limit',
    'recorded-status',
    'recorded-trend',
    'provided-pie',
    'native-history',
    ...[0, 1, 2, 3, 4].map((n) => `followup-identity-${n}`)
  ])
    JSON.parse(readFileSync(join(artifactDir, `${name}.json`), 'utf8'));
}

test('native older pages preserve their cursor, order and unique messages after reload', async ({
  page
}, info) => {
  test.skip(!artifactDir, 'Run native history page qualification first.');
  const pages = JSON.parse(
    readFileSync(join(artifactDir!, 'native-history.json'), 'utf8')
  );
  const id = pages.latest.thread_id;
  await prepareDrawerFixture(page, { voice: false });
  const cursors: string[] = [];
  await page.route('**/api/ai/threads**', async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() !== 'GET') return route.fallback();
    if (url.pathname.endsWith('/threads'))
      return route.fulfill({
        json: {
          threads: [
            { ...pages.latest, last_activity: pages.latest.updated_at }
          ],
          shared_threads: [],
          has_more: false,
          capabilities: {}
        }
      });
    if (!url.pathname.endsWith(`/threads/${id}`)) return route.fallback();
    const before = url.searchParams.get('before_sequence');
    if (before) cursors.push(before);
    return route.fulfill({
      json:
        before === '5'
          ? pages.middle
          : before === '2'
            ? pages.earliest
            : pages.latest
    });
  });
  for (let replay = 0; replay < 2; replay++) {
    await loadDrawerFixture(page);
    await page.getByRole('tab', { name: 'History', exact: true }).click();
    await page
      .getByTestId('ai-chat-history-row')
      .filter({ hasText: 'Native cursor qualification' })
      .click();
    await expect(
      page.getByText('Native saved message 7', { exact: true })
    ).toBeVisible();
    await page.getByRole('button', { name: 'Load earlier messages' }).click();
    await expect(
      page.getByText('Native saved message 2', { exact: true })
    ).toHaveCount(1);
    await page.getByRole('button', { name: 'Load earlier messages' }).click();
    for (let n = 1; n <= 7; n++)
      await expect(
        page.getByText(`Native saved message ${n}`, { exact: true })
      ).toHaveCount(1);
    // DOM order must follow the native sequence; count checks alone miss reversal.
    const text = await page.getByTestId('ai-chat-messages').innerText();
    for (let n = 1; n < 7; n++)
      expect(text.indexOf(`Native saved message ${n}`)).toBeLessThan(
        text.indexOf(`Native saved message ${n + 1}`)
      );
    await expect(
      page.getByRole('button', { name: 'Load earlier messages' })
    ).toHaveCount(0);
  }
  expect(cursors).toEqual(['5', '2', '5', '2']);
  await page.screenshot({ path: info.outputPath('native-history-pages.png') });
});
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

for (const type of ['line', 'bar'] as const)
  for (const theme of ['light', 'dark'] as const)
    test(`home dashboard and ${type} chat fit every resize frame in ${theme}`, async ({
      page
    }, info) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.emulateMedia({
        reducedMotion: 'no-preference',
        colorScheme: theme
      });
      await prepareDrawerFixture(page, {
        voice: false,
        foundation: { threadDetailMessages: [message(type)] }
      });
      await page.goto(
        '/playwright/aichat-drawer.html?native-header=1&actor=reader'
      );
      const widget = page.locator('.dashboard-layout .react-grid-item').first();
      await expect(widget).toBeVisible();
      await page.getByTestId('open-ai-chat').click();
      await page.getByRole('tab', { name: 'History', exact: true }).click();
      await page
        .getByTestId('ai-chat-history-row')
        .filter({ hasText: 'Server conversation' })
        .click();
      const chart = page.getByTestId('presentation-chart');
      await expect(chart).toBeVisible();
      await expect
        .poll(async () => (await widget.boundingBox())!.width)
        .toBeLessThanOrEqual(390);
      for (const size of [
        { width: 320, height: 480 },
        { width: 390, height: 844 }
      ]) {
        await page.setViewportSize(size);
        const samples = await page.evaluate(async () => {
          const result = [];
          for (let n = 0; n < 30; n++) {
            await new Promise<void>((resolve) =>
              requestAnimationFrame(() => resolve())
            );
            const drawer = document
              .querySelector('[data-testid="ai-chat-drawer"]')!
              .getBoundingClientRect();
            const send = document
              .querySelector('[data-testid="send-ai-chat-message"]')!
              .getBoundingClientRect();
            const svg = document
              .querySelector('[data-testid="presentation-chart"] svg')!
              .getBoundingClientRect();
            result.push({
              width: innerWidth,
              document: document.documentElement.scrollWidth,
              drawerFits:
                drawer.right <= innerWidth + 1 &&
                drawer.bottom <= innerHeight + 1,
              sendFits:
                send.right <= innerWidth + 1 && send.bottom <= innerHeight + 1,
              svgFits: svg.right <= innerWidth + 1,
              offenders:
                document.documentElement.scrollWidth > innerWidth + 1
                  ? [...document.querySelectorAll('body *')]
                      .filter(
                        (el) =>
                          el.getBoundingClientRect().right > innerWidth + 1
                      )
                      .slice(0, 10)
                      .map((el) => ({
                        tag: el.tagName,
                        className: el.className,
                        right: el.getBoundingClientRect().right
                      }))
                  : []
            });
          }
          return result;
        });
        writeFileSync(
          info.outputPath(`home-${size.width}-resize.json`),
          JSON.stringify(samples, null, 2)
        );
        expect(
          samples.filter(
            (row) =>
              row.document > row.width + 1 ||
              !row.drawerFits ||
              !row.sendFits ||
              !row.svgFits
          )
        ).toEqual([]);
        await chart.getByRole('row').last().scrollIntoViewIfNeeded();
        await expect(chart.getByRole('row').last()).toBeInViewport();
        await expect(page.getByTestId('send-ai-chat-message')).toBeInViewport();
        await page.screenshot({
          path: info.outputPath(`home-${type}-${theme}-${size.width}.png`)
        });
      }
    });
