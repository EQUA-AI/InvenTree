import { test } from '@playwright/test';

/** Bootstrap reads made by the real ThemeContext/LanguageContext only.
 * Keep the native unexpected-request guard for every other endpoint. */
export function nativeThemeFixtures() {
  test.beforeEach(async ({ page }) => {
    // Native Gantt CSS names remote font fallbacks. Keep fixture runs offline.
    await page.route('https://cdn.svar.dev/fonts/**', (route) =>
      route.fulfill({ status: 204, body: '' })
    );
    const json = (path: string, body: unknown) =>
      page.route(
        (url) => url.pathname === path,
        (route) =>
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
    await json('/api/settings/global/', []);
    await json('/api/generic/status/', {});
    await json('/api/icons/', []);
    await json('/api/ai/threads', { threads: [], shared_threads: [] });
    await json('/api/ai/voice/capability', { enabled: false, voices: [] });
    await json('/api/notifications/', { count: 0, results: [], next: null });
    await json('/api/aichat/ui/capabilities/', {
      version: 1,
      risk_radar: false,
      command_center: false,
      maintenance_metrics: 1
    });
    await page.route('**/api/system-internal/observability/end', (route) =>
      route.request().method() === 'POST'
        ? route.fulfill({ status: 200, json: {} })
        : route.fallback()
    );
  });
}
