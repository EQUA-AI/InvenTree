import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { defineConfig, devices } from '@playwright/test';
import baseline, { harnessSuffix } from './playwright.s0.config';

/** Procurement and Work fixtures only; no database, credentials or backend. */
export default defineConfig({
  ...baseline,
  testMatch: ['r8/*.spec.ts'],
  timeout: 90_000,
  outputDir: resolve(tmpdir(), `aimms-r8-browser${harnessSuffix}`),
  webServer: {
    ...baseline.webServer,
    command: 'node playwright/r8-server.mjs',
    cwd: import.meta.dirname
  },
  use: {
    ...baseline.use,
    timezoneId: 'Asia/Riyadh',
    ...(process.env.AIMMS_BROWSER_WS
      ? {
          connectOptions: {
            wsEndpoint: process.env.AIMMS_BROWSER_WS,
            exposeNetwork: '<loopback>'
          },
          launchOptions: {
            executablePath:
              '/usr/local/bin/playwright-browsers/chromium-1228/chrome-linux/chrome',
            args: ['--no-sandbox']
          }
        }
      : {})
  },
  projects: [
    {
      name: 'r8-desktop',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 900 }
      }
    },
    {
      name: 'r8-phone',
      // Parameterized cases already test their explicit desktop/phone sizes.
      // Keep unparameterized journeys and the tab audit on this project too.
      grepInvert:
        / (?:light|dark) (?:1440|390)$| (?:desktop-light|desktop-dark|phone)$|^phone continuation /,
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 390, height: 844 }
      }
    }
  ]
});
