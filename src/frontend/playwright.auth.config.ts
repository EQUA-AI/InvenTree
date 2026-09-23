import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/pages',
  testMatch: ['pui_auth_csrf.spec.ts', 'pui_session_recovery.spec.ts'],
  workers: 1,
  retries: 0,
  timeout: 30_000,
  reporter: 'list',
  outputDir: 'test-results/auth-csrf',
  use: { baseURL: 'http://127.0.0.1:5173', headless: true },
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
