import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { type Locator, type Page, expect } from '@playwright/test';
import { edgeSweep, textSweep } from './contrast';
import { evidenceRoot, sizes } from './monitoringEvidence';

async function settleCaptureLayout(page: Page) {
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
      )
  );
}

export async function captureState(
  page: Page,
  screen: 'health' | 'performance',
  state: string,
  scheme: 'light' | 'dark',
  feature?: () => Locator,
  base = false
) {
  const root = page.getByTestId(`pump-${screen}`);
  const dir = resolve(
    evidenceRoot,
    'browser/captures',
    screen,
    's8-01-part2-pass2',
    state
  );
  await mkdir(dir, { recursive: true });
  for (const [width, height] of base
    ? sizes
    : [[page.viewportSize()!.width, page.viewportSize()!.height]]) {
    await page.setViewportSize({ width, height });
    const target = feature?.() ?? root;
    await expect(target).toBeVisible();
    await settleCaptureLayout(page);
    await target.evaluate((el) =>
      el.scrollIntoView({
        block: 'start',
        inline: 'center',
        behavior: 'instant'
      })
    );
    // Scrolling can expose rows whose native trend reads affect their layout.
    await settleCaptureLayout(page);
    const framing = await target.evaluate((el, featured) => {
      const chromeBottom = Math.max(
        0,
        ...[...document.querySelectorAll('[style*="position: sticky"]')]
          .map((el) => el.getBoundingClientRect())
          .filter((box) => box.top <= 0 && box.bottom > 0)
          .map((box) => box.bottom)
      );
      const bounds = el.getBoundingClientRect();
      const availableHeight = innerHeight - chromeBottom;
      // A tall phone card may fit with only a few pixels to spare. Reserve
      // symmetric space only when it exists; never move its bottom offscreen
      // merely to keep a fixed 16px gap below the wrapped native header.
      const gutter = featured
        ? Math.max(
            0,
            Math.min(16, Math.floor((availableHeight - bounds.height) / 2))
          )
        : 16;
      // Use the actual top: scrollIntoView can be clamped near document end.
      window.scrollBy({
        top: bounds.top - chromeBottom - gutter,
        behavior: 'instant'
      });
      return {
        chromeBottom,
        viewportBottom: innerHeight,
        availableHeight,
        targetHeight: bounds.height,
        gutter
      };
    }, Boolean(feature));
    if (feature) {
      expect(framing.targetHeight).toBeLessThanOrEqual(framing.availableHeight);
      await expect(target).toBeInViewport({ ratio: 1 });
      const bounds = (await target.boundingBox())!;
      expect(bounds.y).toBeGreaterThanOrEqual(framing.chromeBottom);
      expect(bounds.y + bounds.height).toBeLessThanOrEqual(
        framing.viewportBottom
      );
    }
    await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
    await page.mouse.move(0, 0);
    await page.screenshot({
      path: resolve(dir, `${width}-${scheme}.png`),
      animations: 'disabled'
    });
    const scrollY = await page.evaluate(() => window.scrollY);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: resolve(dir, `${width}-${scheme}-full.png`),
      fullPage: true,
      animations: 'disabled'
    });
    await page.evaluate((y) => window.scrollTo(0, y), scrollY);
    const text = await textSweep(root);
    const edges = await edgeSweep(root);
    const facts = await root.evaluate((root) => {
      const box = (el: Element | null) => {
        const b = el?.getBoundingClientRect();
        return b ? { x: b.x, y: b.y, width: b.width, height: b.height } : null;
      };
      const theme = getComputedStyle(document.documentElement);
      const forbidden = ['red', 'orange', 'yellow', 'green', 'lime', 'pink']
        .flatMap((h) =>
          Array.from({ length: 10 }, (_, i) =>
            theme.getPropertyValue(`--mantine-color-${h}-${i}`).trim()
          )
        )
        .map((hex) => {
          const span = document.createElement('span');
          span.style.color = hex;
          document.body.append(span);
          const rgb = getComputedStyle(span).color;
          span.remove();
          return rgb;
        });
      const palette = [
        ...root.querySelectorAll(
          '.recharts-line-curve,.recharts-bar-rectangle path,[data-heatmap-cell]'
        )
      ].map((el) => ({
        kind: el.tagName,
        stroke: getComputedStyle(el).stroke,
        fill: getComputedStyle(el).fill
      }));
      const table = root.querySelector(
        'table[aria-label="Mapped signal readings"]'
      );
      const rows = table
        ? [...table.querySelectorAll('tbody tr:not([class*="details"])')].map(
            box
          )
        : [];
      const kpis = [...root.querySelectorAll('[data-kpi]')].map((el) => ({
        key: el.getAttribute('data-kpi'),
        box: box(el),
        footer: box(el.querySelector('[data-kpi-footer]'))
      }));
      return {
        viewport: { width: innerWidth, height: innerHeight },
        scroll: { x: scrollX, y: scrollY },
        panelTop: root.getBoundingClientRect().top,
        pageWidth: document.documentElement.scrollWidth,
        table: table
          ? { width: table.clientWidth, scrollWidth: table.scrollWidth }
          : null,
        rows,
        kpis,
        palette,
        paletteFailures: palette.filter(
          (p) => forbidden.includes(p.stroke) || forbidden.includes(p.fill)
        ),
        clockCount: (root.textContent?.match(/Times: your clock/g) ?? [])
          .length,
        forbiddenText: (root as HTMLElement).innerText.match(
          /\bOffline\b|\bConnected\b|\bdegC\b|m3\/h|mH2O|\bNo trend\b/g
        ),
        statusCount: root.querySelectorAll('[role="status"],output').length,
        condition: box(root.querySelector('[data-testid="health-summary"]')),
        active: box(
          [...root.querySelectorAll('h4,h5')].find(
            (el) => el.textContent === 'Active anomalies'
          ) ?? null
        ),
        firstKpi: box(root.querySelector('[data-kpi-value]'))
      };
    });
    await writeFile(
      resolve(dir, `${width}-${scheme}.json`),
      JSON.stringify(
        {
          synthetic: true,
          renderer: 'Linux Chromium (Playwright browser server)',
          ...facts,
          framing,
          text,
          edges
        },
        null,
        2
      )
    );
    expect(facts.pageWidth).toBeLessThanOrEqual(width);
    expect(facts.clockCount).toBe(1);
    expect(facts.forbiddenText).toBeNull();
    expect(facts.paletteFailures).toEqual([]);
    expect(
      text.filter((t) => t.ratio < 4.5),
      'all panel text, including badges'
    ).toEqual([]);
    expect(
      edges.filter((e) => e.ratio < 3),
      'input and switch edges'
    ).toEqual([]);
    expect(facts.statusCount).toBe(1);
    if (width >= 1440 && facts.table)
      expect(facts.table.scrollWidth).toBeLessThanOrEqual(facts.table.width);
    if (base && screen === 'health' && width === 1440) {
      expect(facts.rows).toHaveLength(6);
      expect(
        facts.rows.at(-1)!.y + facts.rows.at(-1)!.height - facts.rows[0]!.y
      ).toBeLessThanOrEqual(360);
    }
    if (
      base &&
      screen === 'performance' &&
      (width === 1100 || width === 1440)
    ) {
      const firstRow = facts.kpis.filter(
        (k) => Math.abs(k.box!.y - facts.kpis[0].box!.y) < 2
      );
      expect(firstRow).toHaveLength(width === 1440 ? 8 : 4);
      const bottoms = firstRow.map((k) => k.footer!.y + k.footer!.height);
      expect(Math.max(...bottoms) - Math.min(...bottoms)).toBeLessThanOrEqual(
        2
      );
    }
    if (base && width === 390) {
      if (screen === 'performance')
        expect(facts.firstKpi!.y + facts.firstKpi!.height).toBeLessThanOrEqual(
          height
        );
      else
        expect(
          await root
            .getByText(/Assessed on 2 of 6 signals/)
            .evaluate((el) => el.getBoundingClientRect().bottom)
        ).toBeLessThanOrEqual(height);
    }
  }
}

export async function auditAxe(
  page: Page,
  screen: 'health' | 'performance',
  name: string
) {
  await page.addScriptTag({ path: resolve(evidenceRoot, 'axe.min.js') });
  const result = await page
    .getByTestId(`pump-${screen}`)
    .evaluate(async (root) => {
      const result = await (window as any).axe.run(root, {
        runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] }
      });
      return {
        violations: result.violations,
        passes: result.passes.length,
        incomplete: result.incomplete
      };
    });
  await writeFile(
    resolve(evidenceRoot, `axe-${name}.json`),
    JSON.stringify(result, null, 2)
  );
  expect(
    result.violations.filter((v: any) =>
      ['serious', 'critical'].includes(v.impact)
    )
  ).toEqual([]);
}

/** SVG horizontal lines have a zero-height DOM box; verify their painted stroke and plot coordinates. */
export async function drawnBoundary(line: Locator) {
  await expect(line).toHaveCount(1);
  const facts = await line.evaluate((el) => {
    const line = el as SVGLineElement;
    const style = getComputedStyle(line);
    const box = line.getBoundingClientRect();
    const chart = line.closest('.recharts-wrapper')!.getBoundingClientRect();
    return {
      width: box.width,
      y: box.y,
      top: chart.top,
      bottom: chart.bottom,
      stroke: style.stroke,
      strokeWidth: Number.parseFloat(style.strokeWidth),
      display: style.display,
      visibility: style.visibility,
      opacity: Number(style.opacity)
    };
  });
  expect(facts.width).toBeGreaterThan(40);
  expect(facts.stroke).not.toBe('none');
  expect(facts.strokeWidth).toBeGreaterThan(0);
  expect(facts.display).not.toBe('none');
  expect(facts.visibility).toBe('visible');
  expect(facts.opacity).toBeGreaterThan(0);
  expect(facts.y).toBeGreaterThanOrEqual(facts.top);
  expect(facts.y).toBeLessThanOrEqual(facts.bottom);
  return facts;
}
