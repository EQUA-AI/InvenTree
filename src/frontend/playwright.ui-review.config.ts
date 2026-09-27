import { defineConfig } from '@playwright/test';

// Real component rendering with fixture APIs on an already running Vite server.
// No backend, login, credentials, or service startup.
export default defineConfig({
  testDir: './tests/pages',
  testMatch: ['pui_ui_reflow.spec.ts'],
  workers: 1,
  retries: 0,
  timeout: 60_000,
  reporter: 'list',
  outputDir: 'test-results/ui-review',
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:5173',
    headless: true,
    screenshot: 'on',
    browserName: 'chromium'
  },
  projects: [
    {
      name: 'desktop-light',
      use: { viewport: { width: 1440, height: 1000 }, colorScheme: 'light' }
    },
    {
      name: 'desktop-dark',
      use: { viewport: { width: 1280, height: 900 }, colorScheme: 'dark' }
    },
    { name: 'narrow-320', use: { viewport: { width: 320, height: 780 } } },
    { name: 'mobile-390', use: { viewport: { width: 390, height: 844 } } },
    {
      name: 'firefox',
      use: { browserName: 'firefox', viewport: { width: 1280, height: 900 } }
    }
  ]
});
