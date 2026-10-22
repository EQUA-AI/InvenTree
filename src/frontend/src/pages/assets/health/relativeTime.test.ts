import { expect, it, vi } from 'vitest';
vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...args: unknown[]) =>
    parts.reduce((s, p, i) => s + p + (args[i] ?? ''), '')
}));
import { relativeAge } from './relativeTime';
it('shares live, stale, future and missing-age rules for every operational time', () => {
  expect(relativeAge(20, true)).toBe('just now');
  expect(relativeAge(20, false)).toBe('20 s old');
  expect(relativeAge(20, true, true)).toBe('20 s old');
  expect(relativeAge(3600)).toBe('1 h old');
  expect(relativeAge(86400)).toBe('1 d old');
  expect(relativeAge(null)).toBe('age unavailable');
  expect(relativeAge(20, true, false, true)).toBe('20 s ahead of your clock');
});
