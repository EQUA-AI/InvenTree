import { defineConfig, devices } from '@playwright/test';

/**
 * Machine location workspace UI acceptance (mocked API responses; no backend
 * and no auth required). Standalone and intentionally minimal: NO webServer
 * and NO globalSetup — it reuses the already-running dev server on
 * http://127.0.0.1:5173 and must not fight other workers for Yarn/ports.
 * Unique machine-* naming keeps it independent of the chat worker's config.
 *
 * Run: node node_modules/@playwright/test/cli.js test \
 *   --config=playwright.machine-locations.config.ts
 */
export default defineConfig({
  testDir: './tests/pages',
  testMatch: ['pui_asset_locations.spec.ts'],
  workers: 1,
  retries: 0,
  timeout: 45_000,
  reporter: 'list',
  outputDir: 'test-results/machine-locations',
  use: {
    baseURL: 'http://127.0.0.1:5173',
    browserName: 'chromium',
    headless: true
  },
  projects: [{ name: 'desktop', use: devices['Desktop Chrome'] }]
});
