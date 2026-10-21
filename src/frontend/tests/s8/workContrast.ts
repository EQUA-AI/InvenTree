import type { Locator } from '@playwright/test';

export async function renderedTextContrast(root: Locator) {
  return root.evaluate((root) => {
    const rgb = (color: string) => (color.match(/[\d.]+/g) ?? []).map(Number);
    const over = (foreground: number[], background: number[]) => {
      const a = foreground[3] ?? 1;
      return foreground
        .slice(0, 3)
        .map((v, index) => v * a + background[index] * (1 - a));
    };
    const backdrop = (node: Element | null): number[] => {
      if (!node) return [255, 255, 255];
      const bg = rgb(getComputedStyle(node).backgroundColor);
      return (bg[3] ?? 1) === 1
        ? bg.slice(0, 3)
        : over(bg, backdrop(node.parentElement));
    };
    const luminance = (color: number[]) =>
      color
        .map((v) => {
          const s = v / 255;
          return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
        })
        .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
    // Containers are sampled only for text they render themselves, so a cell
    // or label holding a badge is measured through the badge, not its parent.
    const ownText = (node: Element) =>
      Array.from(node.childNodes).some(
        (child) =>
          child.nodeType === Node.TEXT_NODE && child.textContent?.trim()
      );
    const containers =
      'td, th, h1, h2, h3, h4, h5, h6, .mantine-Accordion-label, [data-work-warning] .mantine-Text-root';
    return Array.from(
      root.querySelectorAll<HTMLElement>(
        `p, a, .mantine-Badge-root, .mantine-Alert-title, .mantine-Button-label, .mantine-Tabs-tabLabel, ${containers}`
      )
    )
      .filter(
        (node) =>
          node.checkVisibility() &&
          node.textContent?.trim() &&
          !node.closest('[data-disabled]') &&
          (!node.matches(containers) || ownText(node))
      )
      .map((node) => {
        const bg = backdrop(node);
        const fg = over(rgb(getComputedStyle(node).color), bg);
        const l1 = luminance(bg);
        const l2 = luminance(fg);
        return {
          element: node.tagName.toLowerCase(),
          text: node.textContent!.slice(0, 120),
          color: getComputedStyle(node).color,
          background: bg,
          ratio: (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05)
        };
      });
  });
}
