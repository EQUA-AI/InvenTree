import type { VerificationTest } from '@lib/types/WorkExecution';
import { describe, expect, it } from 'vitest';
import { recordedValue, testEvaluation } from './verificationProjection';

const result: VerificationTest = {
  id: 1,
  application_id: 7,
  procedure_revision: 3,
  version: 4,
  status: 'completed',
  value: { number: 0 },
  passed: true,
  note: '',
  disposition_reason: '',
  recorded_by: 'Synthetic tech',
  recorded_at: null,
  events: [],
  step: {
    key: 'one',
    sequence: 1,
    step_type: 'verification',
    title: 'Check',
    instruction: 'Synthetic applicable check',
    required: true,
    required_permission: '',
    value_type: 'number',
    unit: 'mm/s',
    min_value: '0',
    max_value: '4',
    allowed_values: []
  }
};
describe('saved verification qualification', () => {
  it('keeps a recorded zero with sourced bounds', () =>
    expect(testEvaluation(result)).toBe('recorded_pass'));
  it('does not accept an unbounded native numeric pass', () =>
    expect(
      testEvaluation({
        ...result,
        step: { ...result.step, min_value: null, max_value: null }
      })
    ).toBe('not_evaluated'));
  it('retains failure even when criteria are absent', () =>
    expect(
      testEvaluation({
        ...result,
        status: 'failed',
        passed: false,
        step: { ...result.step, min_value: null, max_value: null }
      })
    ).toBe('failed'));
  it('never makes a pending result pass', () =>
    expect(testEvaluation({ ...result, status: 'pending' })).toBe(
      'not_evaluated'
    ));
  it('requires an actual value', () =>
    expect(testEvaluation({ ...result, value: {} })).toBe('not_evaluated'));
  it('does not evaluate missing or nonnumeric structured readings', () => {
    expect(testEvaluation({ ...result, value: { number: null } })).toBe(
      'not_evaluated'
    );
    expect(testEvaluation({ ...result, value: { number: 'unknown' } })).toBe(
      'not_evaluated'
    );
  });
  it('requires a recorded evaluation', () =>
    expect(testEvaluation({ ...result, passed: null })).toBe('not_evaluated'));
  it('retains a not applicable disposition', () =>
    expect(
      testEvaluation({ ...result, status: 'not_applicable', passed: null })
    ).toBe('not_applicable'));
  it('keeps a reopened failure unresolved', () =>
    expect(
      testEvaluation({ ...result, status: 'pending', passed: null, value: {} })
    ).toBe('not_evaluated'));
});
describe('actual values', () => {
  it.each([
    [{ number: 0 }, '0'],
    [{ boolean: false }, 'false'],
    [null, null],
    [{}, null],
    [{ text: 'Unknown cause' }, 'Unknown cause']
  ])('retains %j as %j', (value, expected) =>
    expect(recordedValue(value)).toBe(expected)
  );
});
