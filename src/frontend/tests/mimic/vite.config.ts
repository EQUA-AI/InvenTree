import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { vanillaExtractPlugin } from '@vanilla-extract/vite-plugin';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import { __INVENTREE_VERSION_INFO__ } from '../../version-info';

export default defineConfig({
  root: resolve(__dirname),
  define: __INVENTREE_VERSION_INFO__,
  cacheDir: resolve(tmpdir(), 'inventree-mimic-vite'),
  plugins: [react({ babel: { plugins: ['macros'] } }), vanillaExtractPlugin()],
  resolve: {
    alias: {
      '../../../App': resolve(__dirname, 'api.ts'),
      '../../../../App': resolve(__dirname, 'api.ts'),
      '@lib': resolve(__dirname, '../../lib')
    },
    dedupe: [
      'react',
      'react-dom',
      '@mantine/core',
      '@lingui/core',
      '@tanstack/react-query'
    ]
  },
  server: { host: '127.0.0.1', port: 5187, strictPort: true }
});
