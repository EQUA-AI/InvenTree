/// <reference types="node" />
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { defineConfig, devices } from '@playwright/test';
import baseline, { harnessSuffix } from './playwright.s0.config';

export default defineConfig({
  ...baseline,
  testMatch: ['s7/*.spec.ts'],
  outputDir: resolve(tmpdir(), `aimms-r6-s7-browser${harnessSuffix}`),
  projects: [
    { name: 's7-desktop', use: devices['Desktop Chrome'] },
    {
      name: 's7-phone',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 390, height: 844 }
      }
    }
  ]
});
