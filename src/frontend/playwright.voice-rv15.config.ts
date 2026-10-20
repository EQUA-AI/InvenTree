import { defineConfig, devices } from '@playwright/test';

/**
 * RV15 mounted voice-turn acceptance (mocked API/WebRTC/audio, no backend).
 * Isolated bare Vite dev server on loopback port 5179 — never the default
 * 5173/Django stack, never the production vite.config.ts.
 */
export default defineConfig({
  testDir: './tests/pages',
  testMatch: ['pui_voice_rv15.spec.ts'],
  workers: 1,
  retries: 0,
  // Per-test worker deadline — the acceptance recipe's 30s bound (was 45s).
  timeout: 30_000,
  reporter: 'list',
  outputDir: 'test-results/voice-rv15',
  use: {
    baseURL: 'http://127.0.0.1:5179',
    browserName: 'chromium',
    headless: true
  },
  projects: [{ name: 'desktop', use: devices['Desktop Chrome'] }],
  webServer: {
    command:
      '/home/lokesh/.nvm/versions/node/v26.9.0/bin/node ./node_modules/vite/bin/vite.js --config playwright/voice-rv15.vite.ts --host 127.0.0.1 --port 5179',
    url: 'http://127.0.0.1:5179',
    reuseExistingServer: false,
    timeout: 60_000
  }
});
