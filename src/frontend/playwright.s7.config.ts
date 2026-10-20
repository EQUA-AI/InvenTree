/// <reference types="node" />
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { defineConfig } from '@playwright/test';
import { harnessSuffix } from './playwright.s0.config';
import s6 from './playwright.s6.config';

export default defineConfig({
  ...s6,
  testMatch: ['s7/*.spec.ts'],
  outputDir: resolve(tmpdir(), `aimms-r6-s7-browser${harnessSuffix}`),
  projects: s6.projects?.map((project) => ({
    ...project,
    name: project.name?.replace('s6-', 's7-')
  }))
});
