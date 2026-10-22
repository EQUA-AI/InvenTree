import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it, vi } from 'vitest';
vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...args: unknown[]) =>
    parts.reduce((text, part, i) => text + part + (args[i] ?? ''), '')
}));
import { Timestamp } from './Timestamp';
import { displayClock } from './mimic/observedTime';
const render = (props: Parameters<typeof Timestamp>[0]) =>
  renderToStaticMarkup(
    createElement(MantineProvider, {}, createElement(Timestamp, props))
  );
it('uses the same validated plant clock and date spelling for full instants and dates', () => {
  const props = {
    value: '2026-10-20T08:00:00Z',
    clock: displayClock('Asia/Riyadh')
  };
  expect(render(props)).toContain('20 Oct 2026 11:00:00');
  const date = render({ ...props, dateOnly: true });
  expect(date).toContain('>20 Oct 2026</time>');
  expect(date).not.toContain('>Oct 20, 2026');
});
it('distinguishes absent and invalid timestamps without nesting buttons', () => {
  expect(render({ value: null })).toMatch(/<button[^>]*>—<\/button>/);
  const invalid = render({ value: 'invalid' });
  expect(invalid).toContain('Read failed');
  expect(invalid).not.toMatch(
    /<button\b[^>]*>(?:(?!<\/button>)[\s\S])*<button\b/
  );
});
