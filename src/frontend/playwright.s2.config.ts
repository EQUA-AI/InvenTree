/// <reference types="node" />

import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { defineConfig, devices } from '@playwright/test';

import baseline, { harnessSuffix } from './playwright.s0.config';

/** S2 equipment identity: mocked native surfaces and the reference scene. */
export default defineConfig({
  ...baseline,
  testMatch: ['s2/*.spec.ts'],
  outputDir: resolve(tmpdir(), `aimms-r6-s2-browser${harnessSuffix}`),
  use: {
    ...baseline.use,
    // CI runners have no GPU; recent Chromium only falls back to software
    // WebGL (SwiftShader) when asked. Mocked fixture pages only.
    launchOptions: { args: ['--enable-unsafe-swiftshader'] }
  },
  projects: [
    { name: 's2-desktop', use: devices['Desktop Chrome'] },
    {
      name: 's2-phone',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 390, height: 844 }
      }
    }
  ]
});
