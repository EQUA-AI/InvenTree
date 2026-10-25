import { type Locator, expect } from '@playwright/test';

export async function assertReadableContrast(locator: Locator) {
  const result = await locator.evaluate((node) => {
    const parse = (value: string) => {
      const values = value.match(/[\d.]+/g)?.map(Number) ?? [];
      return [values[0] ?? 0, values[1] ?? 0, values[2] ?? 0, values[3] ?? 1];
    };
    const ancestors: Element[] = [];
    for (
      let ancestor: Element | null = node;
      ancestor;
      ancestor = ancestor.parentElement
    )
      ancestors.push(ancestor);
    const background = ancestors.reverse().reduce(
      (base, ancestor) => {
        const color = parse(getComputedStyle(ancestor).backgroundColor);
        return base.map(
          (channel, index) => color[index] * color[3] + channel * (1 - color[3])
        );
      },
      [255, 255, 255]
    );
    const foreground = parse(getComputedStyle(node).color).slice(0, 3);
    const luminance = (color: number[]) =>
      color
        .map((channel) => {
          const value = channel / 255;
          return value <= 0.04045
            ? value / 12.92
            : ((value + 0.055) / 1.055) ** 2.4;
        })
        .reduce(
          (total, value, index) =>
            total + value * [0.2126, 0.7152, 0.0722][index],
          0
        );
    const bright = Math.max(luminance(foreground), luminance(background));
    const dark = Math.min(luminance(foreground), luminance(background));
    return { foreground, background, ratio: (bright + 0.05) / (dark + 0.05) };
  });
  expect(result.ratio, JSON.stringify(result)).toBeGreaterThanOrEqual(4.5);
  return result;
}
