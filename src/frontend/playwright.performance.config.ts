import { defineConfig } from '@playwright/test';

const port = Number(process.env.AIMMS_HARNESS_PORT || 5188);
const baseURL = `http://127.0.0.1:${port}`;
export default defineConfig({
  testDir: './tests/performance',
  timeout: 60_000,
  retries: 0,
  use: { baseURL, browserName: 'chromium' },
  webServer: {
    command:
      'node node_modules/vite/bin/vite.js --config tests/performance/vite.config.ts',
    url: baseURL,
    reuseExistingServer: true,
    timeout: 120_000
  }
});
