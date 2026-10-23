import { expect, it, vi } from 'vitest';
vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...args: unknown[]) =>
    parts.reduce((s, p, i) => s + p + (args[i] ?? ''), '')
}));
import { relativeAge } from './relativeTime';
it('shares live, stale, future and missing-age rules for every operational time', () => {
  expect(relativeAge(10, true)).toBe('just now');
  expect(relativeAge(11, true)).toBe('11 s ago');
  expect(relativeAge(20, true)).toBe('20 s ago');
  expect(relativeAge(20, false)).toBe('20 s ago');
  expect(relativeAge(20, true, true)).toBe('20 s ago');
  expect(relativeAge(3600)).toBe('1 h ago');
  expect(relativeAge(86400)).toBe('1 d ago');
  expect(relativeAge(null)).toBe('age unavailable');
  expect(relativeAge(20, true, false, true)).toBe('20 s ahead of your clock');
});
