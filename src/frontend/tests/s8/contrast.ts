import { type Locator, expect, test } from '@playwright/test';
type RGB = [number, number, number, number];
const rgba = (value: string): RGB => {
  const c = value.match(/[\d.]+/g)!.map(Number);
  if (value.startsWith('color(srgb ')) for (let i = 0; i < 3; i++) c[i] *= 255;
  return [c[0], c[1], c[2], c[3] ?? 1];
};
const over = (front: RGB, back: RGB): RGB => [
  front[0] * front[3] + back[0] * (1 - front[3]),
  front[1] * front[3] + back[1] * (1 - front[3]),
  front[2] * front[3] + back[2] * (1 - front[3]),
  1
];
const luminance = (color: RGB) =>
  color.slice(0, 3).reduce((sum, value, i) => {
    const s = value / 255;
    return (
      sum +
      (s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4) *
        [0.2126, 0.7152, 0.0722][i]
    );
  }, 0);
function measured(fg: string, chain: string[], fontSize: number) {
  let background: RGB = [255, 255, 255, 1];
  for (const color of chain) background = over(rgba(color), background);
  const foreground = over(rgba(fg), background);
  const a = luminance(foreground);
  const b = luminance(background);
  return {
    ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05),
    foreground,
    background,
    fontSize
  };
}
/** WCAG contrast, compositing every actual rendered ancestor background. */
export async function contrast(locator: Locator) {
  const raw = await locator.evaluate((el) => {
    const chain: string[] = [];
    for (let n: Element | null = el; n; n = n.parentElement)
      chain.unshift(getComputedStyle(n).backgroundColor);
    // Mantine paints the selected segmented label on a floating sibling
    // indicator (or ::before before initialization), not an ancestor.
    const selected = el.closest('.mantine-SegmentedControl-label[data-active]');
    if (selected) {
      const indicator = selected
        .closest('.mantine-SegmentedControl-root')
        ?.querySelector('.mantine-SegmentedControl-indicator');
      chain.push(
        indicator
          ? getComputedStyle(indicator).backgroundColor
          : getComputedStyle(selected, '::before').backgroundColor
      );
    }

    const style = getComputedStyle(el);
    const circle =
      el instanceof SVGTextElement &&
      el.previousElementSibling instanceof SVGCircleElement
        ? el.previousElementSibling
        : null;
    if (circle && getComputedStyle(circle).fill !== 'none')
      chain.push(getComputedStyle(circle).fill);
    return {
      fg: el instanceof SVGTextElement ? style.fill : style.color,
      chain,
      size: Number.parseFloat(style.fontSize)
    };
  });
  return measured(raw.fg, raw.chain, raw.size);
}
export async function readable(locator: Locator, minimumSize = 0) {
  await expect(locator).toBeVisible();
  const actual = await contrast(locator);
  await test.info().attach('computed-contrast', {
    body: JSON.stringify({ text: await locator.textContent(), ...actual }),
    contentType: 'application/json'
  });
  expect(actual.ratio, JSON.stringify(actual)).toBeGreaterThanOrEqual(4.5);
  expect(actual.fontSize).toBeGreaterThanOrEqual(minimumSize);
}
/** Compare a rendered non-text edge against the adjacent rendered surface. */
export async function nonText(locator: Locator, against: Locator) {
  const color = await locator.evaluate(
    (el) => getComputedStyle(el).borderTopColor
  );
  const raw = await against.evaluate((el) => {
    const chain: string[] = [];
    for (let n: Element | null = el; n; n = n.parentElement)
      chain.unshift(getComputedStyle(n).backgroundColor);
    // Mantine paints the selected segmented label on a floating sibling
    // indicator (or ::before before initialization), not an ancestor.
    const selected = el.closest('.mantine-SegmentedControl-label[data-active]');
    if (selected) {
      const indicator = selected
        .closest('.mantine-SegmentedControl-root')
        ?.querySelector('.mantine-SegmentedControl-indicator');
      chain.push(
        indicator
          ? getComputedStyle(indicator).backgroundColor
          : getComputedStyle(selected, '::before').backgroundColor
      );
    }

    return chain;
  });
  const actual = measured(color, raw, 0);
  expect(actual.ratio).toBeGreaterThanOrEqual(3);
  return actual;
}
/** SVG focus edges are painted over the schematic's rendered ancestor surface. */
export async function svgEdge(locator: Locator) {
  const raw = await locator.evaluate((el) => {
    const chain: string[] = [];
    for (let n: Element | null = el; n; n = n.parentElement)
      chain.unshift(getComputedStyle(n).backgroundColor);
    return { stroke: getComputedStyle(el).stroke, chain };
  });
  const actual = measured(raw.stroke, raw.chain, 0);
  await test.info().attach('focus-ring-contrast', {
    body: JSON.stringify(actual),
    contentType: 'application/json'
  });
  expect(actual.ratio).toBeGreaterThanOrEqual(3);
  return actual;
}
/** Direct text and nonempty placeholders; the caller supplies the native panel. */
export async function textSweep(scope: Locator) {
  const raw = await scope.evaluate((root) =>
    [root, ...root.querySelectorAll('*')].flatMap((el) => {
      const style = getComputedStyle(el);
      if (
        !el.getClientRects().length ||
        style.visibility === 'hidden' ||
        style.display === 'none' ||
        el.closest('[aria-hidden="true"], [disabled]')
      )
        return [];
      const text = [...el.childNodes]
        .filter((n) => n.nodeType === Node.TEXT_NODE)
        .map((n) => n.textContent)
        .join(' ')
        .trim();
      const chain: string[] = [];
      for (let n: Element | null = el; n; n = n.parentElement)
        chain.unshift(getComputedStyle(n).backgroundColor);
      // Mantine paints the selected segmented label on a floating sibling
      // indicator (or ::before before initialization), not an ancestor.
      const selected = el.closest(
        '.mantine-SegmentedControl-label[data-active]'
      );
      if (selected) {
        const indicator = selected
          .closest('.mantine-SegmentedControl-root')
          ?.querySelector('.mantine-SegmentedControl-indicator');
        chain.push(
          indicator
            ? getComputedStyle(indicator).backgroundColor
            : getComputedStyle(selected, '::before').backgroundColor
        );
      }

      const results = [];
      const circle =
        el instanceof SVGTextElement &&
        el.previousElementSibling instanceof SVGCircleElement
          ? el.previousElementSibling
          : null;
      if (circle && getComputedStyle(circle).fill !== 'none')
        chain.push(getComputedStyle(circle).fill);
      if (text)
        results.push({
          text,
          fg: el instanceof SVGTextElement ? style.fill : style.color,
          chain,
          size: Number.parseFloat(style.fontSize)
        });
      if (el instanceof HTMLInputElement && el.placeholder) {
        const placeholder = getComputedStyle(el, '::placeholder');
        results.push({
          text: `placeholder: ${el.placeholder}`,
          fg: placeholder.color,
          chain,
          size: Number.parseFloat(placeholder.fontSize)
        });
      }
      return results;
    })
  );
  return raw.map((item) => ({
    text: item.text,
    ...measured(item.fg, item.chain, item.size)
  }));
}

/** Borders/indicators are compared with their adjacent painted surface. */
export async function edgeSweep(scope: Locator) {
  const raw = await scope.evaluate((root) =>
    [
      ...root.querySelectorAll(
        '.mantine-Input-input, .mantine-Switch-track, .mantine-SegmentedControl-indicator, .aimms-twin-hotspot, .aimms-twin-canvas:focus-visible'
      )
    ]
      .filter(
        (el) =>
          el.getClientRects().length &&
          getComputedStyle(el).visibility !== 'hidden' &&
          !el.hasAttribute('hidden')
      )
      .map((el) => {
        const style = getComputedStyle(el);
        const chain: string[] = [];
        const against = el.classList.contains('aimms-twin-hotspot')
          ? el.closest('.aimms-twin-scene')
          : el.parentElement;
        for (let n: Element | null = against; n; n = n.parentElement)
          chain.unshift(getComputedStyle(n).backgroundColor);
        const property = el.classList.contains(
          'mantine-SegmentedControl-indicator'
        )
          ? 'backgroundColor'
          : el.matches(':focus-visible')
            ? 'outlineColor'
            : 'borderTopColor';
        return {
          kind: el.className.toString(),
          property,
          fg: style[property],
          chain
        };
      })
  );
  return raw.map((item) => ({
    kind: item.kind,
    property: item.property,
    ...measured(item.fg, item.chain, 0)
  }));
}
