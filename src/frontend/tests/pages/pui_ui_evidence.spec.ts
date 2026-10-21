import { type Page, expect, test } from '@playwright/test';
import { installVoiceMocks, readMockState, startVoice } from './voice_harness';

const revision = 'a'.repeat(64);
test.beforeEach(async ({ page }) => {
  await page.route('**/*', (route) =>
    ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort()
  );
  await page.route('**/api/aichat/evidence/media/9/metadata/**', (route) =>
    route.fulfill({
      json: {
        revision,
        content_type: 'application/pdf',
        page_count: 2,
        page_labels: ['i', '1']
      }
    })
  );
  // These tests assert the authorized URL/locator contract, not native PDF rendering.
  await page.route('**/api/aichat/evidence/media/9/?*', (route) =>
    route.fulfill({
      contentType: 'application/pdf',
      body: '%PDF-1.4\n%%EOF'
    })
  );
});

test('evidence stops voice, gives one dialog ownership, and restores citation focus', async ({
  page
}) => {
  const voice = await installVoiceMocks(page);
  await page.goto('/playwright/voice-mobile.html');
  await startVoice(page);
  const citation = page.getByRole('button', {
    name: 'Evidence page 2',
    exact: true
  });
  await citation.click();
  const viewer = page.getByTestId('media-evidence-modal');
  await expect(viewer).toContainText('Page 2 of 2');
  await expect(page.locator('object')).toHaveAttribute(
    'data',
    new RegExp(`revision=${revision}#page=2`)
  );
  await expect.poll(() => voice.sessionEnded).toBe(true);
  expect((await readMockState(page)).trackStopped).toBe(true);
  await expect(page.getByTestId('ai-chat-drawer')).toHaveAttribute('inert', '');
  await expect
    .poll(() =>
      page
        .locator('#root')
        .evaluate((element) => element instanceof HTMLElement && element.inert)
    )
    .toBe(true);
  await expect(page.locator('[role="dialog"][aria-modal="true"]')).toHaveCount(
    1
  );
  await page.keyboard.press('Escape');
  await expect(viewer).toHaveCount(0);
  await expect(citation).toBeFocused();
  expect(voice.sessionCreates).toHaveLength(1);
});

test('changing page of the same revision resets the document locator', async ({
  page
}) => {
  await installVoiceMocks(page);
  await page.goto('/playwright/voice-mobile.html');
  await page
    .getByRole('button', { name: 'Evidence page 1', exact: true })
    .click();
  await expect(page.getByTestId('media-evidence-modal')).toContainText(
    'Page 1 of 2'
  );
  await page.evaluate(() =>
    window.dispatchEvent(new Event('fixture-evidence-next'))
  );
  await expect(page.getByTestId('media-evidence-modal')).toContainText(
    'Page 2 of 2'
  );
  await expect(page.locator('object')).toHaveAttribute('data', /#page=2/);
});

test('missing cited revision is unavailable and never fetches replacement bytes', async ({
  page
}) => {
  await installVoiceMocks(page);
  const bytes: string[] = [];
  page.on('request', (request) => {
    if (/\/media\/9\/\?/.test(request.url())) bytes.push(request.url());
  });
  await page.route('**/api/aichat/evidence/media/9/metadata/**', (route) =>
    route.fulfill({ status: 404, json: {} })
  );
  await page.goto('/playwright/voice-mobile.html');
  await page
    .getByRole('button', { name: 'Evidence page 1', exact: true })
    .click();
  await expect(page.getByTestId('media-evidence-unavailable')).toBeVisible();
  expect(bytes).toEqual([]);
  await expect(
    page.getByRole('link', { name: 'Open original', exact: true })
  ).toHaveCount(0);
});

test('invalid page is explicitly source-level and uses no fabricated exact locator', async ({
  page
}) => {
  await installVoiceMocks(page);
  await page.route('**/api/aichat/evidence/media/9/metadata/**', (route) =>
    route.fulfill({
      json: {
        revision,
        content_type: 'application/pdf',
        page_count: 1,
        page_labels: ['1']
      }
    })
  );
  await page.goto('/playwright/voice-mobile.html');
  await page
    .getByRole('button', { name: 'Evidence page 2', exact: true })
    .click();
  await expect(page.getByTestId('media-evidence-modal')).toContainText(
    'Exact page unavailable'
  );
  await expect(
    page.getByRole('link', { name: 'Open original', exact: true })
  ).toHaveAttribute('href', new RegExp(`revision=${revision}$`));
});

const markdownLimit = 512 * 1024;

async function mockMarkdown(page: Page, body: string | Buffer) {
  const bytes = Buffer.isBuffer(body) ? body : Buffer.from(body);
  await page.route('**/api/aichat/evidence/media/9/metadata/**', (route) =>
    route.fulfill({
      json: {
        revision,
        content_type: 'text/markdown',
        page_count: null,
        page_labels: []
      }
    })
  );
  await page.route('**/api/aichat/evidence/media/9/?*', (route) =>
    route.fulfill({
      status: 206,
      contentType: 'text/markdown',
      headers: {
        'Content-Range': `bytes 0-${bytes.length - 1}/${bytes.length}`
      },
      body: bytes
    })
  );
}

async function openMarkdown(page: Page) {
  await installVoiceMocks(page);
  await page.goto('/playwright/voice-mobile.html');
  await page
    .getByRole('button', { name: 'Evidence page 1', exact: true })
    .click();
}

test('Markdown uses revision-pinned bounded bytes and renders GFM without active content', async ({
  page
}) => {
  const reads: { url: string; range: string | undefined }[] = [];
  const imageReads: string[] = [];
  page.on('request', (request) => {
    if (request.url().includes('forbidden-markdown-image'))
      imageReads.push(request.url());
    if (/\/media\/9\/\?/.test(request.url()))
      reads.push({
        url: request.url(),
        range: request.headers().range
      });
  });
  await mockMarkdown(
    page,
    [
      '# Service notes',
      '',
      'Read the **seal procedure**.',
      '',
      '| Part | Status |',
      '| --- | --- |',
      '| Seal | OK |',
      '',
      '```text',
      'torque = 10',
      '```',
      '',
      '[Manual](https://example.invalid/manual)',
      '[Invented record](/machines/999999/)',
      '[Unsafe](javascript:alert(1))',
      '![Blocked image](/forbidden-markdown-image.png)',
      '![External image](https://example.invalid/forbidden-markdown-image.png)',
      '<img src="/forbidden-markdown-image-html.png" onerror="window.markdownExecuted=true">',
      '<script>window.markdownExecuted=true</script>'
    ].join('\n')
  );
  await openMarkdown(page);
  const preview = page.getByTestId('markdown-evidence-preview');
  await expect(
    preview.getByRole('heading', { name: 'Service notes' })
  ).toBeVisible();
  await expect(preview.locator('strong')).toHaveText('seal procedure');
  await expect(
    preview.getByRole('cell', { name: 'Seal', exact: true })
  ).toBeVisible();
  await expect(preview.locator('pre code')).toHaveText('torque = 10');
  await expect(preview.getByRole('link', { name: 'Manual' })).toHaveAttribute(
    'href',
    'https://example.invalid/manual'
  );
  await expect(
    preview.getByRole('link', { name: 'Invented record' })
  ).toHaveCount(0);
  await expect(
    preview.getByRole('link', { name: 'Unsafe', exact: true })
  ).toHaveCount(0);
  await expect(preview.locator('img, script')).toHaveCount(0);
  expect(
    await page.evaluate(() => Reflect.get(window, 'markdownExecuted'))
  ).toBeUndefined();
  expect(imageReads).toEqual([]);
  expect(reads.length).toBeGreaterThan(0);
  for (const read of reads) {
    expect(new URL(read.url).searchParams.get('revision')).toBe(revision);
    expect(read.range).toBe(`bytes=0-${markdownLimit}`);
  }
  await expect(page.locator('object, video')).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Zoom in', exact: true })
  ).toHaveCount(0);
  await expect(
    page.getByRole('link', { name: 'Open original', exact: true })
  ).toHaveAttribute('href', new RegExp(`revision=${revision}$`));
});

test('Markdown accepts exactly 512 KiB', async ({ page }) => {
  const heading = '# Exact limit\n\n';
  const bytes = Buffer.from(
    heading + ' '.repeat(markdownLimit - heading.length)
  );
  expect(bytes.length).toBe(markdownLimit);
  await mockMarkdown(page, bytes);
  await openMarkdown(page);
  await expect(
    page
      .getByTestId('markdown-evidence-preview')
      .getByRole('heading', { name: 'Exact limit' })
  ).toBeVisible();
  await expect(page.getByTestId('markdown-evidence-unavailable')).toHaveCount(
    0
  );
});

for (const [name, body] of [
  ['oversized body', Buffer.alloc(markdownLimit + 1, 0x61)],
  ['malformed UTF-8', Buffer.from([0xc3, 0x28])]
] as const) {
  test(`Markdown fails closed for ${name}`, async ({ page }) => {
    await mockMarkdown(page, body);
    await openMarkdown(page);
    await expect(
      page.getByTestId('markdown-evidence-unavailable')
    ).toBeVisible();
    await expect(page.getByTestId('markdown-evidence-preview')).toHaveCount(0);
    await expect(
      page.getByRole('link', { name: 'Open original', exact: true })
    ).toBeVisible();
  });
}

test('Markdown byte authorization failure never displays a cached preview on reopen', async ({
  page
}) => {
  await mockMarkdown(page, '# Previously authorized');
  await openMarkdown(page);
  await expect(page.getByTestId('markdown-evidence-preview')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('media-evidence-modal')).toHaveCount(0);
  await page.route('**/api/aichat/evidence/media/9/?*', (route) =>
    route.fulfill({ status: 404, body: '' })
  );
  await page
    .getByRole('button', { name: 'Evidence page 1', exact: true })
    .click();
  await expect(page.getByTestId('markdown-evidence-unavailable')).toBeVisible();
  await expect(page.getByText('Previously authorized')).toHaveCount(0);
});

for (const width of [1440, 320]) {
  test(`Markdown wraps prose and contains long code and tables at ${width}px`, async ({
    page
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockMarkdown(
      page,
      `# Long document\n\n${'wrap'.repeat(200)}\n\n| Part | Status |\n| --- | --- |\n| Seal | ${'wide'.repeat(100)} |\n\n\`\`\`text\n${'code'.repeat(200)}\n\`\`\`\n\n${'More notes.\n\n'.repeat(100)}`
    );
    await openMarkdown(page);
    const preview = page.getByTestId('markdown-evidence-preview');
    await expect(preview).toBeVisible();
    const bounds = await preview.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
    expect(bounds!.height).toBeLessThanOrEqual(900 * 0.65 + 1);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth
      )
    ).toBe(true);
    expect(
      await preview.evaluate(
        (element) => element.scrollHeight > element.clientHeight
      )
    ).toBe(true);
  });
}
