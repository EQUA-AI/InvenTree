import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  root: resolve(__dirname),
  cacheDir: resolve(tmpdir(), 'inventree-mimic-vite'),
  plugins: [react({ babel: { plugins: ['macros'] } })],
  resolve: {
    alias: { '../../../App': resolve(__dirname, 'api.ts') },
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
