import { readFileSync } from 'node:fs';
import PO from 'pofile';
import { describe, expect, it } from 'vitest';

describe('English operational source catalogue', () => {
  const catalog = PO.parse(
    readFileSync(new URL('../locales/en/messages.po', import.meta.url), 'utf8')
  );
  it.each([
    'Performance',
    'Equipment view',
    'Motor winding temperature',
    'No active alarms',
    'TEST alarm — Critical'
  ])('retains the source message %s instead of showing its hash', (message) => {
    expect(
      catalog.items.some((item) => item.msgid === message && !item.obsolete)
    ).toBe(true);
  });
});
