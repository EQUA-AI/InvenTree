/// <reference types="node" />

import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { defineConfig, devices } from '@playwright/test';

/**
 * Port of the fixture server. AIMMS_HARNESS_PORT lets parallel checkouts run
 * at once; their output then goes to directories named after the port.
 */
export const harnessPort = Number(process.env.AIMMS_HARNESS_PORT || 15173);
export const harnessSuffix = process.env.AIMMS_HARNESS_PORT
  ? `-${harnessPort}`
  : '';

/** Mocked native surfaces only; no authenticated setup, Django or worker. */
export default defineConfig({
  testDir: './tests',
  testMatch: [
    'pages/pui_asset_locations.spec.ts',
    'pages/pui_ui_reflow.spec.ts',
    's0/pui_*.spec.ts',
    's0/*.s0.ts'
  ],
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 45_000,
  reporter: 'list',
  outputDir: resolve(tmpdir(), `aimms-r6-s0-browser${harnessSuffix}`),
  use: {
    baseURL: `http://127.0.0.1:${harnessPort}`,
    browserName: 'chromium',
    headless: true,
    storageState: { cookies: [], origins: [] },
    serviceWorkers: 'block',
    screenshot: { mode: 'on' },
    trace: 'retain-on-failure'
  },
  webServer: {
    command: 'node node_modules/vite/bin/vite.js --config vite.s0.config.ts',
    url: `http://127.0.0.1:${harnessPort}`,
    reuseExistingServer: false,
    timeout: 120_000
  },
  projects: [
    { name: 's0-desktop', use: devices['Desktop Chrome'] },
    {
      name: 's0-phone',
      testMatch: [
        'pages/pui_ui_reflow.spec.ts',
        's0/pui_supplied_trends.spec.ts',
        's0/pui_native_identity.spec.ts',
        's0/pui_native_views.spec.ts',
        's0/pui_native_work.spec.ts'
      ],
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 390, height: 844 }
      }
    }
  ]
});
