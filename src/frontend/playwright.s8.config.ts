import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { defineConfig, devices } from '@playwright/test';
import baseline, { harnessSuffix } from './playwright.s0.config';

export default defineConfig({
  ...baseline,
  testMatch: ['s8/*.spec.ts'],
  outputDir: resolve(tmpdir(), `aimms-r6-s8-browser${harnessSuffix}`),
  projects: [
    { name: 's8-desktop', use: devices['Desktop Chrome'] },
    {
      name: 's8-phone',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 390, height: 844 }
      }
    }
  ]
});
