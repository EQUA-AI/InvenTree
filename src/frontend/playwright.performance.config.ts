import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/performance',
  timeout: 60_000,
  retries: 0,
  use: { baseURL: 'http://127.0.0.1:5188', browserName: 'chromium' },
  webServer: {
    command: 'npx vite --config tests/performance/vite.config.ts',
    url: 'http://127.0.0.1:5188',
    reuseExistingServer: true,
    timeout: 120_000
  }
});
