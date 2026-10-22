import type { MantineTheme } from '@mantine/core';
import { familyColors } from './TrendChart';

/** A bay keeps its colour across power, flow and filtered chart selections. */
export function pumpColorMap(
  keys: readonly string[],
  theme: MantineTheme,
  scheme: 'light' | 'dark'
) {
  const palette = familyColors(
    14,
    ['blue', 'teal', 'grape', 'cyan', 'orange', 'violet', 'pink'],
    theme,
    scheme
  );
  return new Map(
    keys.map((key) => {
      const number = /(?:^|\D)(\d+)$/.exec(key)?.[1];
      const index = number
        ? Math.max(0, Number(number) - 1)
        : [...key].reduce(
            (hash, character) => (hash * 31 + character.charCodeAt(0)) >>> 0,
            0
          );
      return [key, palette[index % palette.length]];
    })
  );
}
