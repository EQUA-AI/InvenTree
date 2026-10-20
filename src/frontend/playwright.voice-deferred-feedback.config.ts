import { defineConfig } from '@playwright/test';

/*
 * Dedicated config for the voice-deferred-feedback suite (VOICE-H01/H02/H05).
 * Serves ONLY the isolated fixture (tests/voice_deferred_feedback/index.html)
 * on port 5177 — no business backend, no port 5173, and the suite file is
 * named *.pw.ts so the default playwright.config.ts (which starts the full
 * app + Django) never picks it up.
 */
export default defineConfig({
  testDir: './tests/voice_deferred_feedback',
  testMatch: '**/*.pw.ts',
  timeout: 60_000,
  workers: 1,
  reporter: 'line',
  use: {
    browserName: 'chromium',
    baseURL: 'http://localhost:5177'
  },
  webServer: {
    command: 'npx vite --port 5177 --strictPort',
    url: 'http://localhost:5177/tests/voice_deferred_feedback/index.html',
    reuseExistingServer: false,
    timeout: 120_000
  }
});
