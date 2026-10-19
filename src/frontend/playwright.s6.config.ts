/// <reference types="node" />
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { defineConfig, devices } from '@playwright/test';
import baseline, { harnessSuffix } from './playwright.s0.config';

/** S6 Closeout and the one mapped internal maintenance-record command. */
export default defineConfig({
  ...baseline,
  testMatch: ['s6/*.spec.ts'],
  outputDir: resolve(tmpdir(), `aimms-r6-s6-browser${harnessSuffix}`),
  projects: [
    { name: 's6-desktop', use: devices['Desktop Chrome'] },
    {
      name: 's6-phone',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 390, height: 844 }
      }
    }
  ]
});
