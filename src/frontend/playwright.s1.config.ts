/// <reference types="node" />

import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { defineConfig, devices } from '@playwright/test';

import baseline, { harnessSuffix } from './playwright.s0.config';

export default defineConfig({
  ...baseline,
  testMatch: ['s1/*.spec.ts', 'pages/pui_ui_evidence.spec.ts'],
  outputDir: resolve(tmpdir(), `aimms-r6-s1-browser${harnessSuffix}`),
  projects: [
    { name: 's1-desktop', use: devices['Desktop Chrome'] },
    {
      name: 's1-phone',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 390, height: 844 }
      }
    }
  ]
});
