import { defineConfig, devices } from '@playwright/test';

/**
 * VOICE-H03/H08 mounted transport acceptance (mocked API, synthetic media, no
 * backend required). Isolated dev server on port 5179 — never the default
 * 5173 stack and never any other suite's config.
 */
export default defineConfig({
  testDir: './tests/pages',
  testMatch: ['pui_voice_deferred_transport.spec.ts'],
  workers: 1,
  retries: 0,
  timeout: 30_000,
  reporter: 'list',
  outputDir: 'test-results/voice-deferred-transport',
  use: {
    baseURL: 'http://127.0.0.1:5179',
    browserName: 'chromium',
    headless: true
  },
  projects: [{ name: 'desktop', use: devices['Desktop Chrome'] }],
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5179',
    url: 'http://127.0.0.1:5179',
    reuseExistingServer: false
  }
});
