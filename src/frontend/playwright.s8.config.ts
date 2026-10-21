/// <reference types="node" />
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { defineConfig, devices } from '@playwright/test';
import baseline, { harnessSuffix } from './playwright.s0.config';

export default defineConfig({
  ...baseline,
  // Shared-machine module loading is outside the permission assertions' budget.
  timeout: 120_000,
  expect: { timeout: 15_000 },
  testMatch: ['s8/*.spec.ts'],
  outputDir: resolve(tmpdir(), `aimms-r6-s8-browser${harnessSuffix}`),
  projects: [
    {
      name: 's8-desktop',
      testIgnore: ['s8/pui_p31_work_*.spec.ts', 's8/pui_work_uplift*.spec.ts'],
      use: devices['Desktop Chrome']
    },
    {
      name: 's8-phone',
      testIgnore: ['s8/pui_p31_work_*.spec.ts', 's8/pui_work_uplift*.spec.ts'],
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 390, height: 844 }
      }
    },
    {
      name: 's8-work',
      testMatch: ['s8/pui_p31_work_*.spec.ts', 's8/pui_work_uplift*.spec.ts'],
      use: devices['Desktop Chrome']
    }
  ]
});
