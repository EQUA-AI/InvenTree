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
  await expect(scope).toBeVisible();
  await expect
    .poll(() =>
      scope.evaluate((node) => {
        for (
          let ancestor: Element | null = node;
          ancestor;
          ancestor = ancestor.parentElement
        ) {
          const style = getComputedStyle(ancestor);
          if (Number(style.opacity) < 1 || style.visibility !== 'visible')
            return false;
        }
        return true;
      })
    )
    .toBe(true);
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  await page.addScriptTag({ path: source });
  const result = await scope.evaluate(async (node) => {
    const browser = window as unknown as {
      axe: {
        run: (
          context: Element | Document,
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
          passes: {
            id: string;
            nodes: {
              any: { data?: { contrastRatio?: number } }[];
            }[];
          }[];
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
  const measuredContrastNodes = result.passes
    .filter((rule) => rule.id === 'color-contrast')
    .flatMap((rule) => rule.nodes)
    .filter((node) =>
      node.any.some((check) => Number(check.data?.contrastRatio) >= 1)
    );
  expect(
    measuredContrastNodes.length,
    'A contrast audit must measure visible text, not only hidden nodes or incomplete rules.'
  ).toBeGreaterThan(0);
}
