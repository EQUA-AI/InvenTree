import type { CloseoutProposal } from '@lib/types/Closeout';
import { describe, expect, it } from 'vitest';
import { fieldDecisions, savedFieldValue } from './closeoutAccount';
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
