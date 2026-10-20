import type { Page } from '@playwright/test';

/** Exact synthetic global reads made by the production LanguageContext. */
export async function mockTheme(page: Page) {
  for (const [path, json] of [
    [
      '/api/',
      { default_locale: 'en', settings: {}, customize: {}, system_state: {} }
    ],
    ['/api/auth/v1/config', { data: {} }],
    ['/api/settings/user/', []],
    ['/api/settings/global/', []],
    ['/api/generic/status/', {}],
    ['/api/icons/', []],
    ['/api/system-internal/observability/end', {}]
  ] as const)
    await page.route(`**${path}`, (route) => route.fulfill({ json }));
}
