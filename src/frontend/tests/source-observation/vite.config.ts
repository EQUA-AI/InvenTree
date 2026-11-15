import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { vanillaExtractPlugin } from '@vanilla-extract/vite-plugin';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import { __INVENTREE_VERSION_INFO__ } from '../../version-info';

const fixtureRoot = fileURLToPath(new URL('.', import.meta.url));

export default defineConfig({
  root: fixtureRoot,
  define: __INVENTREE_VERSION_INFO__,
  cacheDir: process.env.AIMMS_FIXTURE_CACHE,
  plugins: [
    react({
      exclude: /node_modules|\/vite-cache\//,
      babel: { plugins: ['macros'] }
    }),
    vanillaExtractPlugin()
  ],
  resolve: {
    alias: [
      { find: /^(\.\.\/)+App$/, replacement: resolve(fixtureRoot, 'api.ts') },
      { find: '@lib', replacement: resolve(fixtureRoot, '../../lib') }
    ],
    dedupe: [
      'react',
      'react-dom',
      '@mantine/core',
      '@lingui/core',
      '@tanstack/react-query'
    ]
  },
  server: {
    host: '127.0.0.1',
    port: 5193,
    strictPort: true,
    fs: { allow: [resolve(fixtureRoot, '../..')] }
  }
});
