/** Recording-only component tests: no login, seed, live backend or provider. */
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/pages',
  testMatch: ['pui_voice_session.spec.ts', 'pui_voice_mobile.spec.ts'],
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
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'Pixel 7', use: { ...devices['Pixel 7'] } }
  ],
  webServer: {
    command: 'yarn dev --host 127.0.0.1',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: true
  }
});
