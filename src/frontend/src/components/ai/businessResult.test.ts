import { describe, expect, it } from 'vitest';

import {
  describeFailure,
  genericFailureCode,
  parseBusinessResult
} from './businessResult';

describe('parseBusinessResult', () => {
  it('accepts a 2xx body with no failure signal', () => {
    const result = parseBusinessResult(200, { state: 'executed', receipt: {} });
    expect(result.ok).toBe(true);
  });

  it('never treats HTTP 200 with success=false as done (retired HITL shape)', () => {
    const result = parseBusinessResult(200, {
      success: false,
      status: 'retired',
      message: 'The legacy approval rail is retired and performs no action.'
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.code).toBe('BUSINESS_FAILURE');
      expect(result.committed).toBe('unknown');
    }
  });

  it('reads a proposal failure state and code from a 2xx body', () => {
    const result = parseBusinessResult(200, {
      state: 'failed',
      failure_code: 'PROPOSAL_REVALIDATION_FAILED'
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.code).toBe('PROPOSAL_REVALIDATION_FAILED');
    }
  });

  it('reads a tool-style {success:false, error} body', () => {
    const result = parseBusinessResult(200, {
      success: false,
      error: 'insufficient stock'
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.code).toBe('insufficient stock');
      expect(result.committed).toBe('unknown');
    }
  });

  it('a policy block proves nothing ran', () => {
    const result = parseBusinessResult(200, {
      success: false,
      error: 'blocked_by_policy: recipient not in allow-list',
      blocked_by_policy: true
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.committed).toBe('no');
    }
  });

  it('maps HTTP 410 with the HITL_RETIRED detail to not applied', () => {
    const result = parseBusinessResult(410, {
      detail: { code: 'HITL_RETIRED', message: 'retired' }
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.code).toBe('HITL_RETIRED');
      expect(result.committed).toBe('no');
      expect(result.message).toBe('retired');
    }
  });

  it('maps a FastAPI string detail and a bare 5xx honestly', () => {
    const conflict = parseBusinessResult(409, {
      detail: 'IDEMPOTENCY_CONFLICT'
    });
    expect(conflict.ok).toBe(false);
    if (!conflict.ok) {
      expect(conflict.code).toBe('IDEMPOTENCY_CONFLICT');
      expect(conflict.committed).toBe('unknown');
    }
    const outage = parseBusinessResult(503, null);
    expect(outage.ok).toBe(false);
    if (!outage.ok) {
      expect(outage.code).toBe('HTTP_503');
      expect(outage.committed).toBe('unknown');
    }
  });

  it('a request with no response is unknown, never not-applied', () => {
    const result = parseBusinessResult(0, undefined);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.code).toBe('NETWORK_FAILURE');
      expect(result.committed).toBe('unknown');
    }
  });

  it('honours a caller predicate for domain-specific failure states', () => {
    const result = parseBusinessResult(
      200,
      { state: 'expired' },
      {
        failureCodeOf: (body) =>
          body.state === 'expired' ? 'PROPOSAL_EXPIRED' : null,
        committedOnFailure: () => 'no'
      }
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.code).toBe('PROPOSAL_EXPIRED');
      expect(result.committed).toBe('no');
    }
  });

  it('describes failures with what is known about the effect', () => {
    expect(
      describeFailure({
        ok: false,
        code: 'X',
        httpStatus: 200,
        committed: 'no'
      })
    ).toBe('Not applied: X');
    expect(
      describeFailure({
        ok: false,
        code: 'X',
        httpStatus: 200,
        committed: 'unknown'
      })
    ).toBe('Result unknown: X');
  });

  it('genericFailureCode ignores non-object bodies', () => {
    expect(genericFailureCode('ok')).toBeNull();
    expect(genericFailureCode([])).toBeNull();
    expect(genericFailureCode(null)).toBeNull();
  });
});
