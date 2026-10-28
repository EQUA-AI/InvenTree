import { describe, expect, it } from 'vitest';
import { decisionSectionPreview } from './decisionSectionPreview';

describe('read-only decision preview', () => {
  it('preserves list order, duplicates, punctuation and literal markup', () => {
    const items = [
      'Check fitted cooler; applicability pending.',
      '<b>No authority</b>',
      'Check fitted cooler; applicability pending.'
    ];
    expect(decisionSectionPreview(JSON.stringify(items))).toEqual({
      kind: 'list',
      items
    });
  });

  it('preserves exact gate values without interpreting their authority', () => {
    expect(
      decisionSectionPreview(
        '{"supervisor_review": "pending", "site_safety_authorization": "not authorized"}'
      )
    ).toEqual({
      kind: 'fields',
      fields: [
        ['supervisor_review', 'pending'],
        ['site_safety_authorization', 'not authorized']
      ]
    });
  });

  it.each([
    'Plain source text',
    'not {json}',
    '[]',
    '{}',
    '[""]',
    '{"replacement_parts":0}',
    '["Check", 0]',
    '{"source":{"id":2,"sha256":"abc"}}',
    '{"pending":"yes","pending":"no"}',
    '[9007199254740993]',
    '{"threshold":1.0000000000000001}',
    'null',
    'true',
    '"Applicability pending"'
  ])('retains unsupported or precision-sensitive preview bytes: %s', (text) => {
    expect(decisionSectionPreview(text)).toEqual({ kind: 'text', text });
  });
});
