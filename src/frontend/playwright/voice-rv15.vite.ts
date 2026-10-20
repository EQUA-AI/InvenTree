/**
 * RV15 mounted fixture — bare loopback-only Vite config (port 5179).
 *
 * No production plugins (no codecov/license/istanbul), NO /api proxy to a
 * Django backend — every API response is supplied by Playwright route mocks.
 * React + babel macros and vanilla-extract mirror the real compile contract
 * of the drawer source graph.
 */
import { vanillaExtractPlugin } from '@vanilla-extract/vite-plugin';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Same build globals the production compile defines (vite-env.d.ts declares
// all four). version-info.ts reads ./package.json relative to the process CWD,
// so the dev server must run with the frontend directory as CWD (the
// Playwright webServer command does) — reuse the real module map, never
// guessed/hardcoded literals.
import { __INVENTREE_VERSION_INFO__ } from '../version-info';

export default defineConfig({
  plugins: [react({ babel: { plugins: ['macros'] } }), vanillaExtractPlugin()],
  // Values are already JSON-stringified by version-info.ts, exactly as
  // vite.config.ts/vite.lib.config.ts spread them.
  define: {
    ...__INVENTREE_VERSION_INFO__
  },
  resolve: {
    alias: {
      '@lib': '/lib'
    }
  },
  server: {
    host: '127.0.0.1',
    port: 5179,
    strictPort: true
  }
});
