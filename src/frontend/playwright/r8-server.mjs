import { readFile } from 'node:fs/promises';
import { createServer as createHttpServer } from 'node:http';
import { extname, resolve, sep } from 'node:path';
import { gzipSync } from 'node:zlib';

import { fileURLToPath } from 'node:url';
import { createServer, loadConfigFromFile } from 'vite';
import react from '@vitejs/plugin-react';

const bundleDirectory = process.env.AIMMS_R8_BUNDLE_DIR;
if (bundleDirectory) {
  const root = resolve(bundleDirectory);
  const cache = new Map();
  const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.svg': 'image/svg+xml' };
  const server = createHttpServer(async (request, response) => {
    const pathname = new URL(request.url, 'http://localhost').pathname;
    if (pathname === '/') {
      response.writeHead(200, { 'Content-Type': 'text/plain' });
      response.end('R8 fixtures');
      return;
    }
    try {
      const file = resolve(root, `.${decodeURIComponent(pathname)}`);
      if (!file.startsWith(`${root}${sep}`) || /^\/(api|media|static)(\/|$)/.test(pathname)) {
        response.writeHead(404);
        response.end();
        return;
      }
      let asset = cache.get(file);
      if (!asset) {
        const bytes = await readFile(file);
        asset = { bytes, gzip: gzipSync(bytes), type: types[extname(file)] ?? 'application/octet-stream' };
        cache.set(file, asset);
      }
      const compressed = request.headers['accept-encoding']?.includes('gzip');
      const bytes = compressed ? asset.gzip : asset.bytes;
      response.writeHead(200, {
        'Content-Type': asset.type,
        'Content-Length': bytes.length,
        ...(compressed ? { 'Content-Encoding': 'gzip', Vary: 'Accept-Encoding' } : {})
      });
      response.end(bytes);
    } catch {
      response.writeHead(404);
      response.end();
    }
  });
  server.listen(Number(process.env.AIMMS_HARNESS_PORT || 15173), '127.0.0.1');
} else {
// Avoid writing bundled configuration into a shared node_modules directory.
const configPath = fileURLToPath(new URL('../vite.s0.config.ts', import.meta.url));
const loaded = await loadConfigFromFile(
  { command: 'serve', mode: 'development' },
  configPath,
  undefined,
  undefined,
  undefined,
  'runner'
);
// The fixture cache lives in tmpdir, outside node_modules. Keep Babel on
// application sources so it does not recompile already optimized dependencies.
const plugins = loaded.config.plugins.flat(Infinity).filter(
  (plugin) => !plugin?.name?.startsWith('vite:react-')
);
const server = await createServer({
  ...loaded.config,
  configFile: false,
  plugins: [
    ...plugins,
    react({ include: /\/(src|lib|playwright)\/.*\.[jt]sx?$/, babel: { plugins: ['macros'] } })
  ]
});
await server.listen();
server.printUrls();

}
