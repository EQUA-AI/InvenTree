import { defineConfig, devices } from '@playwright/test';

/**
 * VOICE-H06 mounted-hook acceptance (mocked API, no backend required).
 * Isolated dev server on port 5178 — never the default 5173 stack.
 */
export default defineConfig({
  testDir: './tests/pages',
  testMatch: ['pui_voice_deferred_focus.spec.ts'],
  workers: 1,
  retries: 0,
  timeout: 30_000,
  reporter: 'list',
  outputDir: 'test-results/voice-deferred-focus',
  use: {
    baseURL: 'http://127.0.0.1:5178',
    browserName: 'chromium',
    headless: true
  },
  projects: [{ name: 'desktop', use: devices['Desktop Chrome'] }],
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5178',
    url: 'http://127.0.0.1:5178',
    reuseExistingServer: false
  }
});
