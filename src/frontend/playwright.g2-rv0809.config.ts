import { defineConfig } from '@playwright/test';
for (const name of [
  'G2_NATIVE_BROWSER',
  'G2_NATIVE_JSON',
  'G2_NATIVE_JUNIT',
  'G2_NATIVE_ARTIFACTS'
]) {
  if (!process.env[name])
    throw new Error('G2 requires explicit owned native outputs and browser');
}
export default defineConfig({
  testDir: './tests/pages',
  testMatch: ['pui_voice_g2*.spec.ts', 'pui_voice_rv15.spec.ts'],
  workers: 1,
  retries: 0,
  repeatEach: 1,
  timeout: 30000,
  fullyParallel: false,
  reporter: [
    ['json', { outputFile: process.env.G2_NATIVE_JSON }],
    ['junit', { outputFile: process.env.G2_NATIVE_JUNIT }]
  ],
  outputDir: process.env.G2_NATIVE_ARTIFACTS,
  use: {
    baseURL: 'http://127.0.0.1:5179',
    browserName: 'chromium',
    headless: true,
    launchOptions: {
      executablePath: process.env.G2_NATIVE_BROWSER
    },
    storageState: {
      cookies: [],
      origins: []
    },
    permissions: [],
    screenshot: 'only-on-failure'
  },
  projects: [
    {
      name: 'desktop',
      use: {
        viewport: {
          width: 1280,
          height: 720
        },
        deviceScaleFactor: 1,
        isMobile: false,
        hasTouch: false
      }
    }
  ],
  webServer: {
    command:
      '/usr/bin/node ./node_modules/vite/bin/vite.js --config playwright/g2-rv0809.vite.ts --configLoader runner --host 127.0.0.1 --port 5179',
    url: 'http://127.0.0.1:5179',
    reuseExistingServer: false,
    timeout: 60000
  }
});
