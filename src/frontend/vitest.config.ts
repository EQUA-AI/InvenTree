import { fileURLToPath } from 'node:url';

import { defineConfig } from 'vitest/config';

/**
 * Unit-test runner for PURE frontend modules only (grammar tables, queue and
 * decision reducers, response parsers). No DOM, no React, no Vite plugins:
 * the istanbul/codecov/vanilla-extract/lingui plugins from vite.config.ts
 * are deliberately not loaded here. Browser behaviour stays in Playwright
 * (tests/pages/*.spec.ts).
 *
 * Run: `yarn test:unit` (CI: frontend.yaml "unit" job).
 */
export default defineConfig({
  resolve: {
    alias: {
      '@lib': fileURLToPath(new URL('./lib', import.meta.url))
    }
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
    exclude: ['tests/**', 'node_modules/**', 'dist/**'],
    passWithNoTests: false
  }
});
