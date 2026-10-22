import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { type Page, expect } from '@playwright/test';
export const evidenceRoot = resolve(
  process.env.AIMMS_MONITORING_EVIDENCE_ROOT ??
    '../../data/devscratch/r6-s8/S8-01-part2/pass2'
);
export const sizes = [
  [1100, 800],
  [1440, 900],
  [1920, 1080],
  [390, 844]
] as const;

export async function monitoringMeasurements(
  page: Page,
  screen: 'health' | 'performance'
) {
  return page.getByTestId(`pump-${screen}`).evaluate((root) => {
    const rgb = (color: string) => {
      const values = (color.match(/[\d.]+/g) ?? []).map(Number);
      // CSS color-mix serializes normalized sRGB components, unlike rgb()/rgba().
      return color.startsWith('color(srgb ')
        ? values.map((value, i) => (i < 3 ? value * 255 : value))
        : values;
    };
    const composite = (front: number[], back: number[]) =>
      front
        .slice(0, 3)
        .map((v, i) => v * (front[3] ?? 1) + back[i] * (1 - (front[3] ?? 1)));
    const background = (element: Element): number[] => {
      const layers: number[][] = [];
      let cursor: Element | null = element;
      while (cursor) {
        layers.push(rgb(getComputedStyle(cursor).backgroundColor));
        cursor = cursor.parentElement;
      }
      const selected = element.closest(
        '.mantine-SegmentedControl-label[data-active]'
      );
      if (selected) {
        const indicator = selected
          .closest('.mantine-SegmentedControl-root')
          ?.querySelector('.mantine-SegmentedControl-indicator');
        if (indicator) return rgb(getComputedStyle(indicator).backgroundColor);
      }
      return layers
        .reverse()
        .reduce((back, front) => composite(front, back), [255, 255, 255]);
    };
    const luminance = (values: number[]) =>
      values
        .slice(0, 3)
        .map((v) => {
          const s = v / 255;
          return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
        })
        .reduce((n, v, i) => n + v * [0.2126, 0.7152, 0.0722][i], 0);
    const contrast = (front: number[], back: number[]) => {
      const a = luminance(front);
      const b = luminance(back);
      return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    };
    const text = [...root.querySelectorAll('*')]
      .filter(
        (el) =>
          el.checkVisibility({
            checkOpacity: true,
            checkVisibilityCSS: true
          }) &&
          [...el.childNodes].some(
            (n) => n.nodeType === Node.TEXT_NODE && n.textContent?.trim()
          ) &&
          !el.closest('button:disabled, [data-disabled]')
      )
      .map((el) => {
        const style = getComputedStyle(el);
        const back = background(el);
        const raw = el instanceof SVGElement ? style.fill : style.color;
        const front = composite(rgb(raw), back);
        return {
          text: [...el.childNodes]
            .filter((n) => n.nodeType === Node.TEXT_NODE)
            .map((n) => n.textContent)
            .join('')
            .trim()
            .slice(0, 140),
          ratio: contrast(front, back),
          color: raw,
          background: back,
          tag: el.tagName
        };
      })
      .filter((x) => x.text);
    const inputs = [
      ...root.querySelectorAll(
        'input:not([type=hidden]), .mantine-Switch-track'
      )
    ]
      .filter((el) =>
        el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })
      )
      .map((el) => ({
        name: el.getAttribute('aria-label') || el.id,
        ratio: contrast(
          rgb(getComputedStyle(el).borderTopColor),
          // The track's boundary separates it from its surrounding panel, not its checked fill.
          background(
            el.matches('.mantine-Switch-track') ? el.parentElement! : el
          )
        )
      }));
    return {
      viewport: innerWidth,
      document: document.documentElement.scrollWidth,
      clocks: (root.textContent?.match(/Times: your clock/g) ?? []).length,
      text,
      textFailures: text.filter((x) => x.ratio < 4.5),
      inputs,
      inputFailures: inputs.filter((x) => x.ratio < 3)
    };
  });
}
export async function captureMatrix(
  page: Page,
  screen: 'health' | 'performance',
  state: string,
  scheme: 'light' | 'dark'
) {
  const dir = resolve(
    evidenceRoot,
    'browser',
    'captures',
    screen,
    process.env.AIMMS_CAPTURE_BATCH || '',
    state
  );
  await mkdir(dir, { recursive: true });
  for (const [width, height] of sizes) {
    await page.setViewportSize({ width, height });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: resolve(dir, `${width}-${scheme}.png`),
      animations: 'disabled'
    });
    await page.screenshot({
      path: resolve(dir, `${width}-${scheme}-full.png`),
      fullPage: true,
      animations: 'disabled'
    });
    const measurements = await monitoringMeasurements(page, screen);
    await writeFile(
      resolve(dir, `${width}-${scheme}.json`),
      JSON.stringify(measurements, null, 2)
    );
    expect(measurements.document).toBeLessThanOrEqual(width);
    // Visible clock captions were removed; timestamps retain their clock tooltips.
    expect(measurements.clocks).toBe(0);
    await expect(
      page.getByRole('region', {
        name:
          screen === 'health' ? 'Current condition' : 'Performance workspace',
        exact: true
      })
    ).toBeVisible();
    expect(
      measurements.textFailures,
      'Monitoring text contrast must be at least 4.5:1'
    ).toEqual([]);
    expect(
      measurements.inputFailures,
      'Input and switch borders must be at least 3:1'
    ).toEqual([]);
  }
}
