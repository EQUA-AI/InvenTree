import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it, vi } from 'vitest';
import { FailedValue, MissingValue, ValueTooltip } from './ValueTooltip';
vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...args: unknown[]) =>
    parts.reduce((text, part, i) => text + part + (args[i] ?? ''), '')
}));

const render = (child: ReturnType<typeof createElement>) =>
  renderToStaticMarkup(createElement(MantineProvider, {}, child));

it('keeps present child text as the button name instead of replacing it with its explanation', () => {
  const html = render(
    ValueTooltip({
      label: 'Highest open work priority',
      children: createElement('span', {}, 'High')
    })
  );
  expect(html).toContain('<button');
  expect(html).toContain('type="button"');
  expect(html).toContain('High</span>');
  expect(html).not.toContain('role="img"');
  expect(html).not.toContain('aria-label=');
});

it('distinguishes a failed read from a missing fact and retains a supplied zero with bad quality', () => {
  const failed = render(
    createElement(FailedValue, { reason: 'Source read failed' })
  );
  expect(failed).toContain('Read failed');
  expect(failed).toContain('white-space:nowrap');
  expect(failed).toContain('overflow-wrap:normal');
  expect(failed).not.toContain('>—<');
  const quality = render(
    createElement(FailedValue, { reason: 'Quality flag bad', value: '0 bar' })
  );
  expect(quality).toContain('0 bar');
  expect(quality).toContain('Bad quality');
  expect(quality).not.toContain('Read failed');
});

it('keeps non-focusable facts as plain content without adding a button or tab stop', () => {
  const html = render(
    createElement(MissingValue, {
      reason: 'No observation available',
      focusable: false
    })
  );
  expect(html).toContain('>—</span>');
  expect(html).not.toContain('<button');
  expect(html).not.toContain('tabindex=');
  expect(html).not.toContain('role="img"');
});

it('renders a missing fact as an em dash with the field and reason available to assistive technology', () => {
  const html = render(
    createElement(MissingValue, {
      label: 'Observed',
      reason: 'Observation time not reported by the plant'
    })
  );
  expect(html).toContain(
    'aria-label="Observed: Observation time not reported by the plant"'
  );
  expect(html).toMatch(/<button[^>]*>—<\/button>/);
  expect(html).not.toContain('role="img"');
});
