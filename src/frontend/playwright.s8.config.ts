/// <reference types="node" />
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { defineConfig, devices } from '@playwright/test';
import baseline, { harnessSuffix } from './playwright.s0.config';

export default defineConfig({
  ...baseline,
  timeout: 120_000,
  testMatch: ['s8/pui_*.spec.ts'],
  outputDir: resolve(tmpdir(), `aimms-r6-s8-browser${harnessSuffix}`),
  projects: [{ name: 's8-work', use: devices['Desktop Chrome'] }]
});
