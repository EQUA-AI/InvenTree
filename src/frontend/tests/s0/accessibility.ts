import { writeFileSync } from 'node:fs';
import {
  type Locator,
  type Page,
  type TestInfo,
  expect
} from '@playwright/test';

export async function auditAccessibility(
  page: Page,
  scope: Locator,
  info: TestInfo,
  name: string
) {
  const source = process.env.AIMMS_AXE_SOURCE;
  if (!source) return;
  await page.addScriptTag({ path: source });
  const result = await scope.evaluate(async (node) => {
    const browser = window as unknown as {
      axe: {
        run: (
          context: Element,
          options: { runOnly: { type: 'tag'; values: string[] } }
        ) => Promise<{
          testEngine: { name: string; version: string };
          violations: {
            id: string;
            impact: string;
            description: string;
            nodes: unknown[];
          }[];
          incomplete: unknown[];
          passes: unknown[];
        }>;
      };
    };
    return browser.axe.run(node, {
      runOnly: {
        type: 'tag',
        values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']
      }
    });
  });
  const reportPath = info.outputPath(`accessibility-${name}.json`);
  writeFileSync(reportPath, JSON.stringify(result, null, 2));
  await info.attach(`accessibility-${name}`, {
    path: reportPath,
    contentType: 'application/json'
  });
  expect(result.violations, JSON.stringify(result.violations)).toEqual([]);
}
