import { describe, expect, it } from 'vitest';
import {
  ASSIGNMENT_WARNING_CODES,
  assignmentWarningCode,
  assignmentWarningNeedsReview
} from './workPackageWarnings';

describe('work-package assignment warnings', () => {
  it.each(ASSIGNMENT_WARNING_CODES)(
    'recognizes %s without exposing the transport prefix',
    (code) => {
      expect(assignmentWarningCode(`Assignment review required: ${code}`)).toBe(
        code
      );
    }
  );

  it('fails safely on future assignment codes without displaying their raw token', () => {
    expect(
      assignmentWarningCode(
        'Assignment review required: ASSIGNMENT_FUTURE_REASON'
      )
    ).toBe('ASSIGNMENT_REVIEW_REQUIRED');
  });

  it('does not misclassify other server warnings', () => {
    expect(
      assignmentWarningCode('Check the repair scope before starting.')
    ).toBeNull();
  });

  it('distinguishes a recorded human override from unresolved assignment review', () => {
    expect(assignmentWarningNeedsReview('ASSIGNMENT_MANUAL_OVERRIDE')).toBe(
      false
    );
    expect(assignmentWarningNeedsReview('ASSIGNMENT_QUALIFIED')).toBe(false);
    for (const code of ASSIGNMENT_WARNING_CODES.filter(
      (code) =>
        !['ASSIGNMENT_MANUAL_OVERRIDE', 'ASSIGNMENT_QUALIFIED'].includes(code)
    )) {
      expect(assignmentWarningNeedsReview(code)).toBe(true);
    }
    expect(assignmentWarningNeedsReview('ASSIGNMENT_REVIEW_REQUIRED')).toBe(
      true
    );
    expect(assignmentWarningNeedsReview(null)).toBe(true);
  });
});
