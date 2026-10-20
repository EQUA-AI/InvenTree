import { expect, test } from '@playwright/test';
import { protectedRequests } from '../s0/guards';

const origin = 'http://127.0.0.1:15203';

test('the source bypass still guards every API media static and external URL', () => {
  const matcher = protectedRequests(origin);
  for (const url of [
    `${origin}/api`,
    `${origin}/api?x=1`,
    `${origin}/api/tasks/work-orders/73/`,
    `${origin}/media`,
    `${origin}/media/file.png`,
    `${origin}/static/logo.svg`,
    'http://127.0.0.1:15204/src/main.tsx',
    'https://example.com/src/main.tsx',
    'http://127.0.0.1:15203.evil.example/api/secret',
    'http://127.0.0.1:152030/api/secret',
    'https://127.0.0.1:15203/api/secret'
  ])
    expect(matcher.test(url), url).toBe(true);
});

test('local source files bypass routing without changing native destination policy', () => {
  const matcher = protectedRequests(origin);
  for (const path of [
    '/',
    '/playwright/s0-native.html?surface=machine',
    '/src/states/UserState.tsx',
    '/lib/types/Closeout.ts',
    '/@fs/tmp/aimms-r6-s0-vite/deps/react.js?v=123',
    '/@vite/client',
    '/node_modules/vite/dist/client/env.mjs',
    '/favicon.ico',
    '/apiary/example',
    '/staticity/example'
  ])
    expect(matcher.test(`${origin}${path}`), path).toBe(false);
});
