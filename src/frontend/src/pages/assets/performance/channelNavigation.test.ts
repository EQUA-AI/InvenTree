import { describe, expect, it } from 'vitest';
import { seriesBatches } from './channelNavigation';

describe('bounded aggregate history scope', () => {
  for (const count of [50, 198, 200, 500])
    it(`${count} bindings plus station context respect 200 targets`, () => {
      const bindings = Array.from({ length: count }, (_, i) => i + 1);
      const batches = seriesBatches(bindings, ['/level', '/power']);
      expect(batches).toHaveLength(Math.ceil((count + 2) / 200));
      expect(
        batches.every(
          (batch) =>
            (batch.bindings?.length ?? 0) + (batch.keys?.length ?? 0) <= 200
        )
      ).toBe(true);
      expect(batches.flatMap((batch) => batch.bindings ?? [])).toEqual(
        bindings
      );
      expect(batches.flatMap((batch) => batch.keys ?? [])).toEqual([
        '/level',
        '/power'
      ]);
    });
  it('deduplicates and sorts before partitioning', () => {
    expect(seriesBatches([3, 1, 3], ['z', 'a', 'z'])).toEqual([
      { bindings: [1, 3], keys: ['a', 'z'] }
    ]);
    expect(seriesBatches([], [])).toEqual([]);
  });
});
