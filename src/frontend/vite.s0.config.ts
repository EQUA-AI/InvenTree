/// <reference types="node" />

import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { vanillaExtractPlugin } from '@vanilla-extract/vite-plugin';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

import { __INVENTREE_VERSION_INFO__ } from './version-info';

const root = fileURLToPath(new URL('.', import.meta.url));
// Matches playwright.s0.config.ts: parallel checkouts each use their own port.
const port = Number(process.env.AIMMS_HARNESS_PORT || 15173);
const suffix = process.env.AIMMS_HARNESS_PORT ? `-${port}` : '';

/** Fixture-only baseline: never load the normal backend proxy or dotenv files. */
export default defineConfig({
  root,
  envDir: false,
  envPrefix: 'S0_PUBLIC_',
  cacheDir: resolve(tmpdir(), `aimms-r6-s0-vite${suffix}`),
  plugins: [
    react({ babel: { plugins: ['macros'] } }),
    vanillaExtractPlugin(),
    {
      name: 's0-no-backend',
      configureServer(server) {
        server.middlewares.use((request, response, next) => {
          if (/^\/(api|media|static)(\/|\?|$)/.test(request.url ?? '')) {
            response.statusCode = 503;
            response.end('S0 fixture server: backend access disabled');
            return;
          }
          next();
        });
      }
    }
  ],
  resolve: { alias: { '@lib': resolve(root, 'lib') } },
  define: {
    ...__INVENTREE_VERSION_INFO__,
    'process.env.NODE_ENV': JSON.stringify('development')
  },
  server: {
    host: '127.0.0.1',
    port,
    strictPort: true,
    proxy: {},
    open: false
  }
});
