import { writeFile } from 'node:fs/promises';
import { type Page, expect, test } from '@playwright/test';

export function nativeBaseline(surface?: string) {
  const unexpected = new Map<Page, string[]>();
  const errors = new Map<Page, string[]>();

  test.beforeEach(async ({ page, baseURL }) => {
    const requests: string[] = [];
    const pageErrors: string[] = [];
    unexpected.set(page, requests);
    errors.set(page, pageErrors);
    page.on('pageerror', (error) => pageErrors.push(error.message));
    await page.route('**/*', async (route) => {
      const url = new URL(route.request().url());
      // Native Gantt CSS supplies these optional remote font fallbacks.
      // Fulfil them locally with no bytes; every other external read still fails.
      if (
        url.origin === 'https://cdn.svar.dev' &&
        /^\/fonts\/roboto\/500\.woff2?$/.test(url.pathname)
      ) {
        await route.fulfill({ status: 204, body: '' });
        return;
      }
      if (
        url.origin !== new URL(baseURL!).origin ||
        /^\/(api|media|static)(\/|$)/.test(url.pathname)
      ) {
        requests.push(
          `${route.request().method()} ${url.origin}${url.pathname}`
        );
        await route.fulfill({
          status: 503,
          json: { detail: 'Unmocked S0 request' }
        });
        return;
      }
      if (surface && url.pathname === '/') {
        url.pathname = '/playwright/s0-native.html';
        url.searchParams.set('surface', surface);
        await route.fulfill({
          status: 302,
          headers: { location: url.href },
          body: ''
        });
        return;
      }
      // An explicitly reused local harness can outlive its file watcher. Ask
      // Vite for a fresh source transform without changing API fixture routing.
      const sourceRevision = process.env.AIMMS_SOURCE_REVISION;
      if (
        sourceRevision &&
        /^\/(src|playwright)\/.+\.[cm]?[jt]sx?$/.test(url.pathname)
      ) {
        url.searchParams.set('r6_source_revision', sourceRevision);
        await route.continue({ url: url.href });
        return;
      }
      await route.continue();
    });
  });

  test.afterEach(async ({ page }, testInfo) => {
    const state = await page.evaluate(() => ({
      fixture: window.location.pathname,
      parameters: window.location.search,
      destination:
        document.querySelector('[data-testid="destination"]')?.textContent ??
        null,
      viewportWidth: window.innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      colorScheme: document.documentElement.getAttribute(
        'data-mantine-color-scheme'
      )
    }));
    const evidencePath = testInfo.outputPath('s0-native-state.json');
    await writeFile(
      evidencePath,
      JSON.stringify(
        {
          synthetic: true,
          capturedAt: new Date().toISOString(),
          scenario: testInfo.title,
          project: testInfo.project.name,
          viewport: page.viewportSize(),
          ...state
        },
        null,
        2
      )
    );
    await testInfo.attach('s0-native-state', {
      path: evidencePath,
      contentType: 'application/json'
    });
    expect(
      unexpected.get(page),
      'Every backend/external request needs a fixture'
    ).toEqual([]);
    expect(errors.get(page), 'Native fixture runtime errors').toEqual([]);
    unexpected.delete(page);
    errors.delete(page);
  });
}
