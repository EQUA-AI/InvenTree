import { defineConfig, devices } from '@playwright/test';
export default defineConfig({
  testDir: './tests/pages',
  testMatch: 'pui_maintenance_widgets.spec.ts',
  workers: 1,
  retries: 0,
  timeout: 30_000,
  reporter: 'list',
  outputDir: 'test-results/maintenance-widgets',
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
