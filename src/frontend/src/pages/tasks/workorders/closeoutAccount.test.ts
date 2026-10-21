import type { CloseoutProposal } from '@lib/types/Closeout';
import { describe, expect, it } from 'vitest';
import {
  fieldDecisions,
  reviewedAccountValues,
  savedFieldValue
} from './closeoutAccount';
const proposal: CloseoutProposal = {
  id: 1,
  extractor: 'fixture',
  fields: {
    action: {
      value: 'Replaced seal',
      confidence: 0.9,
      spans: [[0, 13]],
      warnings: []
    }
  },
  decisions: [],
  part_candidates: [],
  warnings: []
};
describe('prepared account provenance', () => {
  it('rehydrates a saved correction before an extraction', () => {
    expect(
      savedFieldValue(
        {
          ...proposal,
          decisions: [
            {
              field_path: 'action',
              decision: 'edited',
              final_value: 'Inspected seal'
            }
          ]
        },
        'action'
      )
    ).toBe('Inspected seal');
  });
  it('a rejected value does not return as a prepared fact', () => {
    expect(
      savedFieldValue(
        {
          ...proposal,
          decisions: [
            { field_path: 'action', decision: 'rejected', final_value: null }
          ]
        },
        'action'
      )
    ).toBe('');
  });
  it('distinguishes unchanged extraction, edited value and manual value', () => {
    expect(
      fieldDecisions(proposal, {
        action: 'Replaced seal',
        result: 'Observed flow',
        cause: ''
      })
    ).toEqual([
      { field_path: 'action', decision: 'accepted' },
      { field_path: 'result', decision: 'edited', final_value: 'Observed flow' }
    ]);
    expect(
      fieldDecisions(proposal, { action: 'Inspected seal' })[0].decision
    ).toBe('edited');
  });
});

it('recovers manual values and later local edits while respecting newer saved decisions', () => {
  expect(
    reviewedAccountValues(
      undefined,
      { action: 'manual', result: 'retained' },
      { result: 'later edit' }
    )
  ).toMatchObject({ action: 'manual', result: 'later edit' });
  const saved = {
    ...proposal,
    decisions: [
      { field_path: 'action', decision: 'edited', final_value: 'new saved' }
    ]
  };
  expect(
    reviewedAccountValues(
      saved,
      { action: 'old saved', result: 'manual' },
      { action: 'local conflicting' }
    )
  ).toEqual({ cause: '', result: 'manual', verification_summary: '' });
  expect(
    reviewedAccountValues(
      saved,
      { action: 'new saved' },
      { action: 'later local' }
    ).action
  ).toBe('later local');
});
