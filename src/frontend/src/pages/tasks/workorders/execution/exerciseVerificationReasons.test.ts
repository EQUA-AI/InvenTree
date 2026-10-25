import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
  exerciseReasonCodes,
  exerciseResultReasonCodes,
  normalizeExerciseReason,
  normalizeExerciseResultReason
} from './exerciseVerificationReasons';

describe('native exercise failure vocabulary', () => {
  it('covers every per-check reason actually emitted by the backend', () => {
    const source = readFileSync(
      '../backend/tasks/work_execution/exercise_verification.py',
      'utf8'
    );
    const emitted = [
      ...source.matchAll(/check\['reasons'\]\.append\('([^']+)'\)/g)
    ].map((match) => match[1]);
    expect([...new Set(emitted)].sort()).toEqual(
      [...exerciseReasonCodes].sort()
    );
  });

  it.each(exerciseReasonCodes)('recognizes native %s', (reason) => {
    expect(normalizeExerciseReason(reason)).toBe(reason);
  });

  it('never treats unknown server text as a trusted operator instruction', () => {
    expect(normalizeExerciseReason('invented_code')).toBe('unknown');
  });
});

describe('native exercise governance vocabulary', () => {
  it('covers all result-level reasons actually emitted by the backend', () => {
    const source = readFileSync(
      '../backend/tasks/work_execution/exercise_verification.py',
      'utf8'
    );
    const emitted = [
      ...source.matchAll(/result\['reasons'\]\.append\('([^']+)'\)/g)
    ].map((match) => match[1]);
    expect(source).toContain("'reasons': ['source_or_ownership_invalid']");
    emitted.push('source_or_ownership_invalid');
    expect([...new Set(emitted)].sort()).toEqual(
      [...exerciseResultReasonCodes].sort()
    );
  });

  it.each(exerciseResultReasonCodes)('recognizes native %s', (reason) => {
    expect(normalizeExerciseResultReason(reason)).toBe(reason);
  });

  it('does not present unknown result-level text as an operator instruction', () => {
    expect(normalizeExerciseResultReason('invented_code')).toBe('unknown');
  });
});
