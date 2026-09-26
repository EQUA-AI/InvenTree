import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  root: resolve(__dirname),
  cacheDir: resolve(tmpdir(), 'inventree-performance-vite'),
  plugins: [react({ babel: { plugins: ['macros'] } })],
  resolve: {
    alias: {
      '@lib': resolve(__dirname, '../../lib'),
      '../../../contexts/ApiContext': resolve(__dirname, 'api.ts'),
      '../../../App': resolve(__dirname, 'api.ts')
    },
    dedupe: [
      'react',
      'react-dom',
      '@mantine/core',
      '@mantine/charts',
      '@lingui/core',
      '@tanstack/react-query'
    ]
  },
  server: { host: '127.0.0.1', port: 5188, strictPort: true }
});
