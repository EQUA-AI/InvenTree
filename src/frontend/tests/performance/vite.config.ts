import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import { __INVENTREE_VERSION_INFO__ } from '../../version-info';

export default defineConfig({
  root: resolve(__dirname),
  envDir: false,
  envPrefix: 'PERFORMANCE_PUBLIC_',
  define: {
    ...__INVENTREE_VERSION_INFO__,
    'process.env.NODE_ENV': JSON.stringify('development')
  },
  cacheDir: resolve(
    tmpdir(),
    `inventree-performance-vite-${process.env.AIMMS_HARNESS_PORT || 5188}`
  ),
  plugins: [react({ babel: { plugins: ['macros'] } })],
  resolve: {
    alias: [
      { find: '@lib', replacement: resolve(__dirname, '../../lib') },
      {
        find: /^(\.\.\/)+contexts\/ApiContext$/,
        replacement: resolve(__dirname, 'api.ts')
      },
      { find: /^(\.\.\/)+App$/, replacement: resolve(__dirname, 'api.ts') }
    ],
    dedupe: [
      'react',
      'react-dom',
      '@mantine/core',
      '@mantine/charts',
      '@lingui/core',
      '@tanstack/react-query'
    ]
  },
  server: {
    host: '127.0.0.1',
    port: Number(process.env.AIMMS_HARNESS_PORT || 5188),
    strictPort: true
  }
});
