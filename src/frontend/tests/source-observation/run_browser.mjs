/** Offline production-component browser controls, with explicitly synthetic transport. */
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';
import { createServer } from 'vite';

const output = process.env.AIMMS_BROWSER_OUTPUT;
const origin = 'http://127.0.0.1:5193';
const errors = [];
const external = [];
const checks = [];
let server;
let browser;
let failure;
try {
  server = await createServer({
    configLoader: 'runner',
    configFile: path.resolve('tests/source-observation/vite.config.ts')
  });
  await server.listen();
  const { payload, body } = await server.ssrLoadModule('/fixture.ts');
  browser = await chromium.launch({
    executablePath: process.env.AIMMS_CHROMIUM,
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage']
  });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    permissions: ['clipboard-read', 'clipboard-write']
  });
  const page = await context.newPage();
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/*', async (route) => {
    const request = route.request();
    if (!request.url().startsWith(origin)) {
      external.push(request.url());
      await route.abort();
      return;
    }
    if (request.url() === `${origin}/synthetic-stream`) {
      const events = [
        {
          type: 'RUN_STARTED',
          threadId: 'fixture-thread',
          runId: 'fixture-run'
        },
        { type: 'CUSTOM', name: 'aimms.sourceObservation', value: payload },
        {
          type: 'TEXT_MESSAGE_START',
          messageId: 'source-fixture',
          role: 'assistant'
        },
        {
          type: 'TEXT_MESSAGE_CONTENT',
          messageId: 'source-fixture',
          delta: body
        },
        { type: 'TEXT_MESSAGE_END', messageId: 'source-fixture' },
        {
          type: 'RUN_FINISHED',
          threadId: 'fixture-thread',
          runId: 'fixture-run'
        }
      ];
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')
      });
      return;
    }
    await route.continue();
  });
  await page.goto(origin, { waitUntil: 'networkidle', timeout: 90000 });
  await page.getByTestId('source-observation').waitFor();
  assert.match(
    await page.getByTestId('source-observation').innerText(),
    /Full population unknown/
  );
  assert.match(
    await page.getByTestId('source-observation').innerText(),
    /Selected window incomplete/
  );
  await page.screenshot({
    path: path.join(output, 'desktop-recorded.png'),
    fullPage: true
  });
  await page.getByRole('button', { name: 'Copy answer', exact: true }).click();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  assert.match(copied, /Full population unknown/);
  assert.match(copied, /Synthetic fixture/);
  assert.match(copied, /source_unavailable/);
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export answer as Markdown' }).click();
  const download = await downloadPromise;
  const downloaded = path.join(output, 'answer.md');
  await download.saveAs(downloaded);
  assert.equal(await readFile(downloaded, 'utf8'), copied);
  checks.push(
    'desktop copy and actual Markdown download retain the same observation'
  );
  await page
    .getByRole('button', { name: 'Stream fixture', exact: true })
    .click();
  await page
    .getByRole('status')
    .filter({ hasText: 'Synthetic AG-UI stream complete' })
    .waitFor();
  await page.getByRole('button', { name: 'Copy answer', exact: true }).click();
  assert.equal(
    await page.evaluate(() => navigator.clipboard.readText()),
    copied
  );
  checks.push('installed SDK stream, fixture reload and copy/export agree');
  for (const [name, match, file] of [
    [
      'Access unavailable',
      /Source unavailable under current access/,
      'desktop-unavailable.png'
    ],
    [
      'Read error',
      /The source read could not be completed/,
      'desktop-error.png'
    ],
    ['Empty window', /0 samples returned/, 'desktop-empty.png']
  ]) {
    await page.getByRole('button', { name, exact: true }).click();
    assert.match(await page.locator('body').innerText(), match);
    await page.screenshot({ path: path.join(output, file), fullPage: true });
    if (name === 'Access unavailable') {
      assert.doesNotMatch(
        await page.locator('body').innerText(),
        /12\.5|synthetic-sample-1|Recorded bearing/
      );
    }
  }
  checks.push(
    'unavailable projection removes prior content and provenance; error and empty states remain distinct'
  );
  await page
    .getByRole('button', { name: 'Reload fixture', exact: true })
    .click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: path.join(output, 'phone-recorded.png'),
    fullPage: true
  });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth
    ),
    false
  );
  await page.keyboard.press('Tab');
  assert.equal(
    await page.evaluate(() => document.activeElement?.tagName),
    'BUTTON'
  );
  await page.screenshot({
    path: path.join(output, 'phone-keyboard.png'),
    fullPage: true
  });
  checks.push(
    'phone has no horizontal overflow and native actions are keyboard reachable'
  );
  await page
    .getByRole('button', { name: 'Access unavailable', exact: true })
    .click();
  await page.screenshot({
    path: path.join(output, 'phone-unavailable.png'),
    fullPage: true
  });
  assert.deepEqual(errors, []);
  assert.deepEqual(external, []);
} catch (error) {
  failure = error.stack;
  process.exitCode = 1;
} finally {
  await browser?.close();
  await server?.close();
  await writeFile(
    path.join(output, 'browser-results.json'),
    JSON.stringify(
      {
        checks,
        errors,
        external,
        failure: failure ?? null,
        limits:
          'Production assistant component and installed SDK with synthetic local transport. Not deployed UI or native HTTP authorization acceptance.'
      },
      null,
      2
    )
  );
  if (failure) console.error(failure);
}
