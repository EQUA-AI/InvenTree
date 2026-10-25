import { defineConfig, devices } from '@playwright/test';

/** Mocked browser tests: real components and API contracts, no backend required. */
export default defineConfig({
  testDir: './tests/pages',
  testMatch: ['pui_staff_directory.spec.ts'],
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: 'list',
  outputDir: 'test-results/staff-directory',
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:5173',
    headless: true
  },
  projects: [{ name: 'desktop', use: devices['Desktop Chrome'] }]
});
