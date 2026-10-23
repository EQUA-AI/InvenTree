import { defineConfig, devices } from '@playwright/test';

/**
 * Machine-metrics UI acceptance (mocked API responses; no backend required).
 * Mirrors playwright.maintenance.config.ts: chromium only, desktop plus a
 * real phone profile for the responsive-layout checks, single worker for Vite
 * HMR compatibility.
 */
const port = process.env.METRICS_TEST_PORT ?? '5173';
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: './tests/pages',
  testMatch: ['pui_machine_metrics.spec.ts'],
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: 'list',
  outputDir: 'test-results/machine-metrics',
  use: {
    baseURL,
    browserName: 'chromium',
    headless: true
  },
  projects: [
    { name: 'desktop', use: devices['Desktop Chrome'] },
    { name: 'Pixel 7', use: devices['Pixel 7'] }
  ],
  webServer: {
    command: `node node_modules/vite/bin/vite.js --host 127.0.0.1 --port ${port} --strictPort`,
    url: baseURL,
    reuseExistingServer: true
  }
});
