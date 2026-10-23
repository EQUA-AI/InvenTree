import { DEFAULT_THEME } from '@mantine/core';
import { expect, it } from 'vitest';
import { pumpColorMap } from './pumpColors';

it.each(['light', 'dark'] as const)(
  'keeps a bay colour in every chart and filtered selection in %s',
  (scheme) => {
    const keys = Array.from({ length: 14 }, (_, index) => `P${index + 1}`);
    const all = pumpColorMap(keys, DEFAULT_THEME, scheme);
    const filtered = pumpColorMap(['P14', 'P2', 'P7'], DEFAULT_THEME, scheme);
    expect(new Set(all.values()).size).toBe(14);
    for (const [key, color] of filtered) expect(color).toBe(all.get(key));
  }
);
