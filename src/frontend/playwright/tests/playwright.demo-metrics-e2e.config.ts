import { defineConfig, devices } from '@playwright/test';

/**
 * REAL demo-metrics acceptance config: PostgreSQL -> Django -> browser.
 *
 * No webServer here — contrib/container/demo-metrics-e2e.sh owns the lifecycle
 * of the real backend (dedicated `inventree_dm_e2e_v3` database, dedicated
 * loopback port) so every API response the browser sees is produced by the
 * real server. The spec uses zero page.route mocks.
 */
const BASE_URL = process.env.DM_E2E_BASE_URL || 'http://127.0.0.1:8127';

export default defineConfig({
  testDir: '.',
  testMatch: ['pui_demo_metrics_e2e.spec.ts'],
  workers: 1,
  retries: 0,
  timeout: 90_000,
  reporter: 'list',
  outputDir: './demo-metrics-e2e-results',
  use: {
    baseURL: BASE_URL,
    browserName: 'chromium',
    headless: true
  },
  projects: [
    { name: 'desktop', use: devices['Desktop Chrome'] },
    // Pixel 7 asserts the real mobile shell + wire-level scoped API data
    // (see the spec) — it is not a desktop-UI check.
    { name: 'Pixel 7', use: devices['Pixel 7'] }
  ]
});
