/** Recording-only component tests: no login, seed, live backend or provider. */
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/pages',
  testMatch: 'pui_voice_session.spec.ts',
  workers: 1,
  retries: 0,
  timeout: 30_000,
  reporter: 'list',
  outputDir: process.env.VOICE_TEST_OUTPUT ?? 'test-results/voice',
  use: {
    baseURL: 'http://127.0.0.1:5173',
    browserName: 'chromium',
    headless: true
  },
  webServer: {
    command: 'yarn dev --host 127.0.0.1',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: true
  }
});
