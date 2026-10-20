import { type Locator, type Page, expect } from '@playwright/test';

export async function humanNames(scope: Locator) {
  for (const role of ['button', 'checkbox', 'combobox'] as const) {
    for (const control of await scope.getByRole(role).all()) {
      if (!(await control.isVisible())) continue;
      await expect(control).toHaveAccessibleName(/\S/);
      await expect(control).not.toHaveAccessibleName(
        /^[a-z\d]+(?:-[a-z\d]+)+$/
      );
    }
  }
}

/** Intersect actual outline strokes with every ancestor's clipping rectangle. */
export async function visibleScrollFocus(viewport: Locator) {
  const ring = await viewport.evaluate((node) => {
    const r = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    const width = Number.parseFloat(style.outlineWidth);
    const offset = Number.parseFloat(style.outlineOffset);
    const clip = { left: 0, top: 0, right: innerWidth, bottom: innerHeight };
    for (
      let parent = node.parentElement;
      parent;
      parent = parent.parentElement
    ) {
      const s = getComputedStyle(parent);
      const b = parent.getBoundingClientRect();
      if (/hidden|clip|auto|scroll/.test(s.overflowX)) {
        clip.left = Math.max(clip.left, b.left + parent.clientLeft);
        clip.right = Math.min(
          clip.right,
          b.left + parent.clientLeft + parent.clientWidth
        );
      }
      if (/hidden|clip|auto|scroll/.test(s.overflowY)) {
        clip.top = Math.max(clip.top, b.top + parent.clientTop);
        clip.bottom = Math.min(
          clip.bottom,
          b.top + parent.clientTop + parent.clientHeight
        );
      }
    }
    const outer = {
      left: r.left - offset - width,
      top: r.top - offset - width,
      right: r.right + offset + width,
      bottom: r.bottom + offset + width
    };
    const strokes = [
      [outer.left, outer.top, outer.right, outer.top + width],
      [outer.left, outer.bottom - width, outer.right, outer.bottom],
      [outer.left, outer.top, outer.left + width, outer.bottom],
      [outer.right - width, outer.top, outer.right, outer.bottom]
    ];
    return {
      style: style.outlineStyle,
      width,
      offset,
      area: strokes.reduce(
        (area, [left, top, right, bottom]) =>
          area +
          Math.max(0, Math.min(right, clip.right) - Math.max(left, clip.left)) *
            Math.max(
              0,
              Math.min(bottom, clip.bottom) - Math.max(top, clip.top)
            ),
        0
      )
    };
  });
  expect(ring.style).not.toBe('none');
  expect(ring.width).toBeGreaterThanOrEqual(1);
  expect(ring.offset).toBeLessThanOrEqual(-2);
  expect(
    ring.area,
    'focus outline survives ancestor/viewport clipping'
  ).toBeGreaterThan(8);
}

export async function scrollStops(page: Page) {
  for (const viewport of await page
    .locator('[data-work-scroll-viewport]')
    .all()) {
    if (!(await viewport.isVisible())) continue;
    const overflowing = () =>
      viewport.evaluate(
        (node) =>
          node.scrollWidth - node.clientWidth > 1 ||
          node.scrollHeight - node.clientHeight > 1
      );
    const overflow = await overflowing();
    await expect(viewport).toHaveAttribute('tabindex', overflow ? '0' : '-1');
    await expect(viewport).toHaveAttribute('role', 'group');
    if (overflow) {
      await page.keyboard.press('Tab');
      await viewport.focus();
      await expect(viewport).toBeFocused();
      await visibleScrollFocus(viewport);
    }
  }
}

export async function unclippedBadges(scope: Locator) {
  expect(
    await scope.locator('.mantine-Badge-root').evaluateAll((roots) =>
      roots
        .filter((root) => {
          if (!root.checkVisibility()) return false;
          const label = root.querySelector('.mantine-Badge-label');
          if (!label) return false;
          const b = root.getBoundingClientRect();
          const s = getComputedStyle(root);
          if (!/hidden|clip/.test(s.overflow)) return false;
          const radius = Math.min(
            Number.parseFloat(s.borderTopLeftRadius),
            b.width / 2,
            b.height / 2
          );
          const inside = (x: number, y: number) => {
            if (
              x < b.left - 0.5 ||
              x > b.right + 0.5 ||
              y < b.top - 0.5 ||
              y > b.bottom + 0.5
            )
              return false;
            const cx =
              x < b.left + radius
                ? b.left + radius
                : x > b.right - radius
                  ? b.right - radius
                  : x;
            const cy =
              y < b.top + radius
                ? b.top + radius
                : y > b.bottom - radius
                  ? b.bottom - radius
                  : y;
            return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2 + 1;
          };
          const walker = document.createTreeWalker(label, NodeFilter.SHOW_TEXT);
          for (let text = walker.nextNode(); text; text = walker.nextNode()) {
            const content = text.textContent ?? '';
            for (let i = 0; i < content.length; i++) {
              if (!content[i].trim()) continue;
              const range = document.createRange();
              range.setStart(text, i);
              range.setEnd(text, i + 1);
              for (const r of range.getClientRects()) {
                if (
                  ![
                    [r.left + 1, r.top + 1],
                    [r.right - 1, r.top + 1],
                    [r.left + 1, r.bottom - 1],
                    [r.right - 1, r.bottom - 1]
                  ].every(([x, y]) => inside(x, y))
                )
                  return true;
              }
            }
          }
          return false;
        })
        .map((n) => n.textContent)
    )
  ).toEqual([]);
}

export async function noTooltipOverlap(page: Page, content: Locator) {
  const b = await content.boundingBox();
  expect(b).not.toBeNull();
  for (const tooltip of await page.getByRole('tooltip').all()) {
    if (!(await tooltip.isVisible())) continue;
    const t = await tooltip.boundingBox();
    expect(
      t &&
        b &&
        t.x < b.x + b.width &&
        t.x + t.width > b.x &&
        t.y < b.y + b.height &&
        t.y + t.height > b.y,
      'tooltip covers message or physical-authority boundary'
    ).toBe(false);
  }
}
