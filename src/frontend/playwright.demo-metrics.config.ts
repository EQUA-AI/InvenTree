import { defineConfig, devices } from '@playwright/test';

/**
 * Demo-metrics UI acceptance (mocked API responses; no backend required).
 * Mirrors playwright.maintenance.config.ts: chromium only, desktop plus a
 * real phone profile for the responsive-layout checks, single worker for Vite
 * HMR compatibility.
 */
export default defineConfig({
  testDir: './tests/pages',
  testMatch: ['pui_demo_metrics.spec.ts'],
  workers: 1,
  retries: 0,
  timeout: 30_000,
  reporter: 'list',
  outputDir: 'test-results/demo-metrics',
  use: {
    baseURL: 'http://127.0.0.1:5173',
    browserName: 'chromium',
    headless: true
  },
  projects: [
    { name: 'desktop', use: devices['Desktop Chrome'] },
    { name: 'Pixel 7', use: devices['Pixel 7'] }
  ],
  webServer: {
    command: 'yarn dev --host 127.0.0.1',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: true
  }
});
