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
    ['/api/notifications/?*', { count: 0, results: [], next: null }],
    ['/api/ai/threads?*', { threads: [], shared_threads: [] }],
    ['/api/ai/voice/capability', { enabled: false, voices: [] }],
    [
      '/api/aichat/ui/capabilities/',
      {
        version: 1,
        risk_radar: false,
        command_center: false,
        maintenance_metrics: 1,
        backend_commit: 's8-synthetic'
      }
    ],
    ['/api/system-internal/observability/end', {}]
  ] as const)
    await page.route(`**${path}`, (route) => route.fulfill({ json }));
}
