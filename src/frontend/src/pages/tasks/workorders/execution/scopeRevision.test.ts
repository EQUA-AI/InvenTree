import type { PackageScope } from '@lib/types/WorkExecution';
import { describe, expect, it } from 'vitest';
import { compareScopeText, effectiveScope } from './scopeRevision';

const scope = (id: number, current: boolean): PackageScope => ({
  id,
  version: id,
  current,
  verified_cause: '',
  lines: [],
  crew_size: null,
  planned_elapsed_minutes: null,
  approved_by: null,
  approved_at: '',
  note: ''
});

describe('retained scope facts', () => {
  it('keeps added, removed, changed and identical ordinal text distinct', () => {
    const changes = compareScopeText(
      [
        { sequence: 1, action: 'Same' },
        { sequence: 2, action: 'Before' },
        { sequence: 4, action: 'Removed' }
      ],
      [
        { sequence: 1, action: 'Same' },
        { sequence: 2, action: 'After' },
        { sequence: 3, action: 'Added' }
      ]
    );
    expect(changes.map((change) => change.state)).toEqual([
      'same',
      'changed',
      'added',
      'removed'
    ]);
    expect(changes[2].before).toBeNull();
    expect(changes[3].after).toBeNull();
  });
  it('does not infer task identity or unaffected readiness from moved text', () => {
    expect(
      compareScopeText(
        [{ sequence: 1, action: 'Inspect' }],
        [{ sequence: 2, action: 'Inspect' }]
      ).map((row) => row.state)
    ).toEqual(['removed', 'added']);
  });
  it('selects only the sole current and latest native version', () => {
    expect(effectiveScope([scope(1, false), scope(2, true)], 2)?.id).toBe(2);
    expect(effectiveScope([scope(1, true), scope(2, false)], 1)).toBeNull();
    expect(effectiveScope([scope(1, true), scope(2, true)], 2)).toBeNull();
    expect(effectiveScope([scope(1, false), scope(2, true)], 1)).toBeNull();
    expect(effectiveScope([], null)).toBeNull();
  });
});
