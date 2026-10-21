/// <reference types="node" />

import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { defineConfig, devices } from '@playwright/test';

import baseline, { harnessSuffix } from './playwright.s0.config';

/** S4 Work execution panels: mocked native surfaces only. */
export default defineConfig({
  ...baseline,
  testMatch: ['s4/*.spec.ts'],
  outputDir: resolve(tmpdir(), `aimms-r6-s4-browser${harnessSuffix}`),
  projects: [
    { name: 's4-desktop', use: devices['Desktop Chrome'] },
    {
      name: 's4-phone',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 390, height: 844 }
      }
    }
  ]
});
