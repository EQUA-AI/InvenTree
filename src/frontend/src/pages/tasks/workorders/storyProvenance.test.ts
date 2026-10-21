import { describe, expect, it } from 'vitest';
import { narrativeExcerpts } from './storyProvenance';

describe('narrative source offsets', () => {
  it('uses Python Unicode code points, including supplementary characters', () => {
    expect(
      narrativeExcerpts('A🔧 seal', {
        value: 'seal',
        spans: [[3, 7]],
        confidence: 1,
        warnings: []
      })
    ).toEqual([{ start: 3, end: 7, text: 'seal' }]);
  });
  it('rejects malformed ranges and never invents audio timing', () => {
    expect(
      narrativeExcerpts('text', {
        value: '',
        spans: [
          [-1, 2],
          [1, 9],
          [2, 2],
          [0.5, 2]
        ],
        confidence: 1,
        warnings: []
      })
    ).toEqual([]);
    expect(narrativeExcerpts('text')).toEqual([]);
  });
});
