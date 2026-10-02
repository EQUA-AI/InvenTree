import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/mimic',
  testMatch: '*.spec.ts',
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:5187', browserName: 'chromium' },
  webServer: {
    command: 'node node_modules/.bin/vite --config tests/mimic/vite.config.ts',
    url: 'http://127.0.0.1:5187',
    reuseExistingServer: false
  }
});
