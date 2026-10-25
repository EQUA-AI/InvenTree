import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { defineConfig, devices } from '@playwright/test';
import baseline, { harnessSuffix } from './playwright.s0.config';

process.env.AIMMS_MIMIC_ENTRY ??= '/playwright/s0-native.html?surface=mimic';

export default defineConfig({
  ...baseline,
  testMatch: [
    's3/pui_test_alarm.spec.ts',
    's3/pui_pump_warning_links.spec.ts',
    's2/pui_equipment_scene.spec.ts',
    's6/pui_final_update.spec.ts',
    's6/pui_service_report_workflow.spec.ts',
    's6/pui_pump08_intake.spec.ts',
    's6/pui_automatic_test_work.spec.ts',
    'mimic/pui_mimic.spec.ts',
    'mimic/pui_mimic_layout.spec.ts'
  ],
  outputDir: resolve(tmpdir(), `aimms-test-alarm${harnessSuffix}`),
  use: {
    ...baseline.use,
    timezoneId: 'Asia/Riyadh',
    ...(process.env.AIMMS_BROWSER_EXECUTABLE
      ? {
          launchOptions: {
            executablePath: process.env.AIMMS_BROWSER_EXECUTABLE,
            args: ['--no-sandbox', '--enable-unsafe-swiftshader']
          }
        }
      : {}),
    ...(process.env.AIMMS_BROWSER_WS
      ? {
          connectOptions: {
            wsEndpoint: process.env.AIMMS_BROWSER_WS,
            exposeNetwork: '<loopback>'
          }
        }
      : {})
  },
  projects: [
    { name: 'desktop', use: devices['Desktop Chrome'] },
    {
      name: 'phone',
      testIgnore: '**/mimic/pui_mimic_layout.spec.ts',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 390, height: 844 }
      }
    }
  ]
});
