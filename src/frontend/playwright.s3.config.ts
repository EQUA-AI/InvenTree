/// <reference types="node" />

import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { defineConfig, devices } from '@playwright/test';

import baseline, { harnessSuffix } from './playwright.s0.config';

/** S3 Health and Performance fidelity: mocked native surfaces only. */
export default defineConfig({
  ...baseline,
  testMatch: ['s3/*.spec.ts'],
  outputDir: resolve(tmpdir(), `aimms-r6-s3-browser${harnessSuffix}`),
  projects: [
    { name: 's3-desktop', use: devices['Desktop Chrome'] },
    {
      name: 's3-phone',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 390, height: 844 }
      }
    }
  ]
});
