/**
 * G2 RV08/RV09 private mounted baseline — bare loopback-only Vite config.
 *
 * Single runtime variable changed versus the frozen voice-rv15.vite.ts
 * contract: cacheDir now CONTAINS a /node_modules/ segment. The installed
 * @vitejs/plugin-react (dist/index.js line 88) sets
 * `const defaultExcludeRE = /\/node_modules\//;` and filters Babel through
 * it, so optimized vendor chunks cached under a path without /node_modules/
 * were re-transformed (parent49 observed Babel deoptimizing huge optimized
 * React/Sentry/icon bundles and a 25386ms Navigate). Same plugins, version
 * map, alias and strict loopback server as the frozen fixture.
 */
import { vanillaExtractPlugin } from '@vanilla-extract/vite-plugin';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

import { __INVENTREE_VERSION_INFO__ } from '../version-info';

const cacheDir = process.env.G2_VITE_CACHE_DIR;
if (!cacheDir || !cacheDir.includes('/node_modules/')) {
  throw new Error('G2 requires an explicit owned cache beneath node_modules');
}

export default defineConfig({
  plugins: [react({ babel: { plugins: ['macros'] } }), vanillaExtractPlugin()],
  define: {
    ...__INVENTREE_VERSION_INFO__
  },
  resolve: {
    alias: {
      '@lib': '/lib'
    }
  },
  cacheDir,
  server: {
    host: '127.0.0.1',
    port: 5179,
    strictPort: true
  }
});
