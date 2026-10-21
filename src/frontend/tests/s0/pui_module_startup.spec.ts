import { expect, test } from '@playwright/test';
import { nativeBaseline } from './guards';
import { mockMachine } from './machineFixtures';

nativeBaseline();

// Exercise Vite's browser-side CommonJS interop. A bundled build or a Node
// import can hoist dayjs ahead of initialization and conceal this startup bug.
test('investigation component evaluates through the Vite dev module loader', async ({
  page
}) => {
  await mockMachine(page);
  await page.goto(
    '/playwright/s0-native.html?surface=machine&entry=/machines/machine/18/details/'
  );
  const componentType = await page.evaluate(async () => {
    const source = '/src/pages/assets/investigation/InvestigationSlices.tsx';
    const component = await import(/* @vite-ignore */ source);
    return typeof component.InvestigationSlices;
  });
  expect(componentType).toBe('function');
  await expect(page.getByText('Serial Number', { exact: true })).toBeVisible();
});
