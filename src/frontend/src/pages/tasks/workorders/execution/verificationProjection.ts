import type { VerificationTest } from '@lib/types/WorkExecution';

/** A UI label can only qualify a recorded result; it cannot accept a test. */
export function testEvaluation(
  test: VerificationTest
): 'failed' | 'recorded_pass' | 'not_evaluated' | 'not_applicable' {
  if (test.status === 'failed' || test.passed === false) return 'failed';
  if (test.status === 'not_applicable') return 'not_applicable';
  const step = test.step;
  const hasBound = [step.min_value, step.max_value].some(
    (value) => value !== null && value !== '' && Number.isFinite(Number(value))
  );
  const hasCriteria =
    !!step.instruction.trim() && (step.value_type !== 'number' || hasBound);
  const actual = recordedValue(test.value);
  const hasValue =
    actual !== null &&
    (step.value_type !== 'number' ||
      (actual.trim() !== '' && Number.isFinite(Number(actual))));
  return test.status === 'completed' &&
    test.passed === true &&
    hasCriteria &&
    hasValue
    ? 'recorded_pass'
    : 'not_evaluated';
}

/** Preserve a false boolean and zero reading, without relabelling raw text. */
export function recordedValue(value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null;
  if (
    typeof value === 'number' ||
    typeof value === 'string' ||
    typeof value === 'boolean'
  )
    return String(value);
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    for (const key of ['number', 'boolean', 'choice', 'text']) {
      if (key in record) return recordedValue(record[key]);
    }
    if (Object.keys(record).length === 0) return null;
  }
  return JSON.stringify(value);
}
