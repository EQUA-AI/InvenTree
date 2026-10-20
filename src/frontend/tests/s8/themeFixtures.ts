import { test } from '@playwright/test';

/** Bootstrap reads made by the real ThemeContext/LanguageContext only.
 * Keep the native unexpected-request guard for every other endpoint. */
export function nativeThemeFixtures() {
  test.beforeEach(async ({ page }) => {
    const json = (path: string, body: unknown) =>
      page.route(`**${path}`, (route) =>
        route.request().method() === 'GET'
          ? route.fulfill({ json: body })
          : route.fallback()
      );
    await json('/api/', {
      default_locale: null,
      plugins_enabled: false,
      settings: {}
    });
    await json('/api/auth/v1/config', { data: {} });
    await json('/api/settings/user/', []);
    await page.route('**/api/settings/global/', async (route) => {
      if (route.request().method() !== 'GET') return route.fallback();
      const referer = route.request().headers().referer;
      const zone = referer
        ? new URL(referer).searchParams.get('plant_timezone')
        : null;
      await route.fulfill({
        json: zone ? [{ key: 'AIMMS_PLANT_TIMEZONE', value: zone }] : []
      });
    });
    await json('/api/generic/status/', {});
    await json('/api/icons/', []);
    await page.route('**/api/system-internal/observability/end', (route) =>
      route.request().method() === 'POST'
        ? route.fulfill({ status: 200, json: {} })
        : route.fallback()
    );
  });
}
