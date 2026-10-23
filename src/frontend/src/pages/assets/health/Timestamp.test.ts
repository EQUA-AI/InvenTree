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

it('keeps a valid timestamp as plain text without an extra keyboard stop', () => {
  const markup = render({ value: '2026-10-20T08:00:00Z' });
  expect(markup).toContain('<time');
  expect(markup).not.toContain('<button');
});

it('keeps a relative column compact while retaining the full observation instant', () => {
  const markup = render({
    value: '2026-10-20T08:00:00Z',
    relative: 'just now',
    relativeOnly: true
  });
  expect(markup).toContain('>just now</time>');
  expect(markup).toContain('<button');
  expect(markup).toContain('dateTime="2026-10-20T08:00:00.000Z"');
  expect(markup).toContain('white-space:nowrap');
});

it('keeps civil maintenance dates on their supplied calendar day in all plant zones', () => {
  for (const zone of ['America/Los_Angeles', 'Asia/Riyadh']) {
    expect(
      render({ value: '2026-09-18', dateOnly: true, clock: displayClock(zone) })
    ).toContain('>18 Sept 2026</time>');
  }
});

it('exposes full instant and zone to AT even when a relative table cell uses its row control', () => {
  const html = render({
    value: '2026-10-20T08:00:00Z',
    relative: '20 s ago',
    relativeOnly: true,
    focusable: false,
    clock: displayClock('Asia/Riyadh')
  });
  expect(html).not.toContain('<button');
  expect(html).toContain('20 Oct 2026 11:00:00');
  expect(html).toContain('Asia/Riyadh');
  expect(html).toContain('mantine-VisuallyHidden');
});
