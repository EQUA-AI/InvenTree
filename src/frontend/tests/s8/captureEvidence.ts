import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import type { Page, TestInfo } from '@playwright/test';
import { edgeSweep, textSweep } from './contrast';

export async function captureEvidence(
  page: Page,
  info: TestInfo,
  screen: string,
  state: string,
  scheme: string,
  width: number
) {
  const batch = process.env.AIMMS_CAPTURE_BATCH;
  if (batch && !/^[a-z0-9-]+$/.test(batch))
    throw new Error('Invalid capture batch');
  const dir = batch
    ? resolve(
        '../../data/devscratch/r6-s8/S8-01',
        screen,
        'browser',
        batch,
        state
      )
    : info.outputPath(screen, state);
  await mkdir(dir, { recursive: true });
  const stem = resolve(dir, `${width}-${scheme}`);
  const panel = page.locator('[data-operational-panel]').first();
  const contrast = (await panel.count()) ? await textSweep(panel) : [];
  const edges = (await panel.count()) ? await edgeSweep(panel) : [];
  const layout = await page.evaluate(() => {
    const box = (selector: string) => {
      const el = document.querySelector(selector);
      if (!el) return null;
      const b = el.getBoundingClientRect();
      return {
        x: b.x,
        y: b.y,
        width: b.width,
        height: b.height,
        bottom: b.bottom
      };
    };
    const visible = (el: Element) =>
      el.getClientRects().length > 0 &&
      getComputedStyle(el).visibility !== 'hidden';
    const tableWidths = [
      ...document.querySelectorAll('.mantine-TableScrollContainer-viewport')
    ]
      .filter(visible)
      .map((el) => ({
        clientWidth: el.clientWidth,
        scrollWidth: el.scrollWidth
      }));
    const rows = [
      ...document.querySelectorAll(
        'tr[data-occurrence], [data-machine-card], table tbody tr'
      )
    ]
      .filter(visible)
      .map((el) => el.getBoundingClientRect())
      .filter((r) => r.y >= 0 && r.bottom <= window.innerHeight);
    const fullyVisible = (selector: string) =>
      [...document.querySelectorAll(selector)]
        .filter(visible)
        .map((el) => el.getBoundingClientRect())
        .filter((r) => r.y >= 0 && r.bottom <= window.innerHeight).length;
    const svg = document.querySelector<SVGSVGElement>(
      'svg[aria-label="Pump unit schematic"]'
    );
    const tags = [
      ...document.querySelectorAll<HTMLElement>(
        '[data-unit-schematic] [data-reading]'
      )
    ].map((el) => {
      const rect = el.getBoundingClientRect();
      const label = el.querySelector<HTMLElement>('[data-tag-label]')!;
      const labelRect = label.getBoundingClientRect();
      return {
        pointer: el.dataset.point,
        x: rect.x,
        y: rect.y,
        right: rect.right,
        bottom: rect.bottom,
        labelSize: Number.parseFloat(getComputedStyle(label).fontSize),
        valueOverlapsLabel:
          labelRect.bottom >
          el.querySelector('[data-value]')!.getBoundingClientRect().top + 1,
        labelOutsideTag:
          labelRect.left < rect.left - 1 || labelRect.right > rect.right + 1
      };
    });
    const callouts = [
      ...document.querySelectorAll<HTMLButtonElement>('button[data-part]')
    ].map((el) => {
      const r = el.getBoundingClientRect();
      return {
        code: el.dataset.part,
        x: r.x,
        y: r.y,
        right: r.right,
        bottom: r.bottom
      };
    });
    const partLabels = [
      ...document.querySelectorAll<HTMLElement>(
        'button[data-part] [data-part-label]'
      )
    ].map((el) => {
      const rect = el.closest('button')!.getBoundingClientRect();
      const label = el.getBoundingClientRect();
      return {
        text: el.textContent,
        lineClamp: getComputedStyle(el).webkitLineClamp,
        hardTruncated: getComputedStyle(el).textOverflow === 'ellipsis',
        outside:
          label.left < rect.left - 1 ||
          label.right > rect.right + 1 ||
          label.top < rect.top - 1 ||
          label.bottom > rect.bottom + 1
      };
    });
    const markers = [
      ...(svg?.querySelectorAll<SVGCircleElement>(
        '[data-part-markers] circle'
      ) ?? [])
    ].map((el) => {
      const r = el.getBoundingClientRect();
      return { x: r.x, y: r.y, right: r.right, bottom: r.bottom };
    });
    const intersects = (
      a: { x: number; y: number; right: number; bottom: number },
      b: { x: number; y: number; right: number; bottom: number }
    ) => a.x < b.right && a.right > b.x && a.y < b.bottom && a.bottom > b.y;
    return {
      page: {
        clientWidth: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth
      },
      tables: tableWidths,
      fold: {
        visibleRows: rows.length,
        machineRows: fullyVisible('[data-machine]'),
        occurrenceRows: fullyVisible('tr[data-occurrence]'),
        firstMachine: box('[data-machine]'),
        pageFacts: box('[aria-label="Loaded page summary"] output'),
        componentsHeading: box('ul[aria-label="Model components"]'),
        keyReadings: box('[aria-label="Key readings"]'),
        svg: box('svg[aria-label="Pump unit schematic"]'),
        canvas: box('.aimms-twin-canvas'),
        inspector: box('[aria-label="Selected position"]'),
        components: box('ul[aria-label="Model components"]'),
        evidence: box('[data-testid="occurrence-evidence"]'),
        openedState: box(
          '[data-testid="occurrence-evidence"] [data-alarm-status]'
        ),
        occurrenceHeader: box('table thead')
      },
      drawing: {
        tags,
        partLabels,
        calloutOverlaps: callouts.flatMap((callout, i) =>
          callouts
            .slice(i + 1)
            .filter((other) => intersects(callout, other))
            .map((other) => [callout.code, other.code])
        ),
        drawingOverlaps: svg
          ? [...tags.map((tag) => ({ ...tag, code: tag.pointer })), ...callouts]
              .filter((el) => intersects(el, svg.getBoundingClientRect()))
              .map((el) => el.code)
          : [],
        tagOverlaps: tags.flatMap((tag, i) =>
          tags
            .slice(i + 1)
            .filter((other) => intersects(tag, other))
            .map((other) => [tag.pointer, other.pointer])
        ),
        markerOverlaps: tags
          .filter((tag) => markers.some((marker) => intersects(tag, marker)))
          .map((tag) => tag.pointer)
      },
      clockStatements: [
        ...document.querySelectorAll('[data-operational-panel] *')
      ]
        .filter((el) =>
          [...el.childNodes].some(
            (n) =>
              n.nodeType === Node.TEXT_NODE &&
              /your clock|plant clock/.test(n.textContent ?? '')
          )
        )
        .filter(visible).length
    };
  });
  const metadata = {
    synthetic: true,
    renderer: process.env.AIMMS_RENDERER ?? 'Chromium · Playwright runner',
    sourceRevision: process.env.AIMMS_SOURCE_REVISION ?? null,
    route: await page.url(),
    mount:
      'Real Layout, MachineIndex and MachineDetail with ThemeContext; intercepted fixture APIs; no app login or database',
    screen,
    state,
    scheme,
    width,
    contrast,
    contrastFailures: contrast.filter((item) => item.ratio < 4.5),
    nonText: edges,
    nonTextFailures: edges.filter((item) => item.ratio < 3),
    ...layout
  };
  await writeFile(`${stem}.json`, JSON.stringify(metadata, null, 2));
  await page.screenshot({ path: `${stem}.png`, animations: 'disabled' });
  await page.screenshot({
    path: `${stem}-full.png`,
    fullPage: true,
    animations: 'disabled'
  });
  return { stem, metadata };
}
