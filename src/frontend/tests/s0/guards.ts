import { writeFile } from 'node:fs/promises';
import { type Page, expect, test } from '@playwright/test';

/** Match every protected/external read without routing local Vite modules. */
export function protectedRequests(origin: string): RegExp {
  const escaped = origin.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(
    `^(?:${escaped}/(?:api|media|static)(?:/|\\?|$)|(?!${escaped}(?:/|$)).+)`
  );
}

export function nativeBaseline(
  surface?: string,
  options: { routeLocalSources?: boolean } = {}
) {
  const unexpected = new Map<Page, string[]>();
  const errors = new Map<Page, string[]>();

  test.beforeEach(async ({ page, baseURL }) => {
    const requests: string[] = [];
    const pageErrors: string[] = [];
    unexpected.set(page, requests);
    errors.set(page, pageErrors);
    page.on('pageerror', (error) => pageErrors.push(error.message));
    const origin = new URL(baseURL!).origin;
    const sourceRevision = process.env.AIMMS_SOURCE_REVISION;
    const isSource = (url: URL) =>
      /^\/(src|lib|playwright)\/.+\.[cm]?[jt]sx?$/.test(url.pathname);
    const escapedOrigin = origin.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const intercepted = new RegExp(
      [
        `^(?!${escapedOrigin}(?:/|$))`,
        `^${escapedOrigin}/(?:api|media|static)(?:/|\\?|$)`,
        ...(surface ? [`^${escapedOrigin}/(?:\\?|$)`] : []),
        ...(sourceRevision
          ? [
              `^${escapedOrigin}/(?:src|lib|playwright)/.+\\.[cm]?[jt]sx?(?:\\?|$)`
            ]
          : [])
      ].join('|')
    );
    // Allowed local assets need no interception. Keep every request that can
    // affect the guard, fixture redirect or explicit source refresh routed.
    // (P32's routeLocalSources option is kept for callers; this narrowed
    // matcher already routes only protected, external, redirect and
    // source-refresh requests.)
    void options;
    await page.route(intercepted, async (route) => {
      const url = new URL(route.request().url());
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
      if (sourceRevision && isSource(url)) {
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
