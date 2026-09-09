/**
 * Business-result parsing for action endpoints (voice-UX plan A8).
 *
 * A 2xx transport status is necessary but never sufficient: the retired
 * approval rail answered 200 with `success=false`, proposal confirms can
 * return a failed state, and tools report `{success: false}` bodies. Every
 * caller that shows "done" must go through here and also decide what it
 * KNOWS about the effect: `committed` is 'no' only when the server proves
 * nothing ran, 'unknown' otherwise. Pure module (unit-tested with vitest).
 */

export type Committed = 'yes' | 'no' | 'unknown';

export interface BusinessOk<T> {
  ok: true;
  data: T;
  httpStatus: number;
}

export interface BusinessFailure {
  ok: false;
  code: string;
  httpStatus: number;
  committed: Committed;
  message?: string;
}

export type BusinessResult<T> = BusinessOk<T> | BusinessFailure;

export interface BusinessSpec<T> {
  /**
   * Extra failure predicate for a 2xx body: return the failure code when the
   * body means "not done", null when it means success. Runs AFTER the generic
   * checks (success === false, error, failure_code, state === 'failed').
   */
  failureCodeOf?: (body: T) => string | null;
  /** What a 2xx business failure says about the effect (default 'unknown'). */
  committedOnFailure?: (body: T) => Committed;
}

const FAILED_STATES = new Set(['failed', 'error', 'retired']);

function asRecord(body: unknown): Record<string, unknown> | null {
  return body !== null && typeof body === 'object' && !Array.isArray(body)
    ? (body as Record<string, unknown>)
    : null;
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value.trim() : null;
}

/** The failure code a body carries, or null when the body reads as success. */
export function genericFailureCode(body: unknown): string | null {
  const record = asRecord(body);
  if (record === null) {
    return null;
  }
  if (record.success === false) {
    return (
      nonEmptyString(record.failure_code) ??
      nonEmptyString(record.code) ??
      nonEmptyString(record.error) ??
      'BUSINESS_FAILURE'
    );
  }
  const failureCode = nonEmptyString(record.failure_code);
  if (failureCode !== null) {
    return failureCode;
  }
  const error = nonEmptyString(record.error);
  if (error !== null) {
    return error;
  }
  const state = nonEmptyString(record.state);
  if (state !== null && FAILED_STATES.has(state.toLowerCase())) {
    return `STATE_${state.toUpperCase()}`;
  }
  return null;
}

/** What a failing body proves about the effect. */
export function genericCommitted(body: unknown, httpStatus: number): Committed {
  const record = asRecord(body);
  if (record !== null) {
    if (record.effect_committed === true) {
      return 'yes';
    }
    if (
      record.effect_committed === false ||
      record.blocked_by_policy === true
    ) {
      return 'no';
    }
    const detail = asRecord(record.detail);
    if (detail !== null && nonEmptyString(detail.code) === 'HITL_RETIRED') {
      return 'no';
    }
  }
  if (
    httpStatus === 401 ||
    httpStatus === 403 ||
    httpStatus === 404 ||
    httpStatus === 410 ||
    httpStatus === 422
  ) {
    // Refused before any work.
    return 'no';
  }
  return 'unknown';
}

function transportFailureCode(body: unknown, httpStatus: number): string {
  const record = asRecord(body);
  if (record !== null) {
    const detail = record.detail;
    const detailRecord = asRecord(detail);
    if (detailRecord !== null) {
      const code = nonEmptyString(detailRecord.code);
      if (code !== null) {
        return code;
      }
    }
    const direct =
      nonEmptyString(detail) ??
      nonEmptyString(record.code) ??
      nonEmptyString(record.error) ??
      nonEmptyString(record.failure_code);
    if (direct !== null) {
      return direct;
    }
  }
  return `HTTP_${httpStatus}`;
}

/**
 * Decide whether an action endpoint's answer means the action was DONE.
 *
 * `httpStatus` 0 means the request never got a response (network failure,
 * timeout): the effect is unknown, never "not applied".
 */
export function parseBusinessResult<T>(
  httpStatus: number,
  body: T,
  spec: BusinessSpec<T> = {}
): BusinessResult<T> {
  if (httpStatus === 0) {
    return {
      ok: false,
      code: 'NETWORK_FAILURE',
      httpStatus,
      committed: 'unknown'
    };
  }
  if (httpStatus < 200 || httpStatus >= 300) {
    return {
      ok: false,
      code: transportFailureCode(body, httpStatus),
      httpStatus,
      committed: genericCommitted(body, httpStatus),
      message: messageOf(body)
    };
  }
  const code = genericFailureCode(body) ?? spec.failureCodeOf?.(body) ?? null;
  if (code !== null) {
    return {
      ok: false,
      code,
      httpStatus,
      committed:
        spec.committedOnFailure?.(body) ?? genericCommitted(body, httpStatus),
      message: messageOf(body)
    };
  }
  return { ok: true, data: body, httpStatus };
}

function messageOf(body: unknown): string | undefined {
  const record = asRecord(body);
  if (record === null) {
    return undefined;
  }
  const detail = asRecord(record.detail);
  return (
    nonEmptyString(record.message) ??
    nonEmptyString(detail?.message) ??
    nonEmptyString(record.error) ??
    undefined
  );
}

/** Short user-facing line for a failure: what happened and what is known. */
export function describeFailure(failure: BusinessFailure): string {
  const what =
    failure.committed === 'no'
      ? 'Not applied'
      : failure.committed === 'yes'
        ? 'Applied, but the response reported a problem'
        : 'Result unknown';
  return `${what}: ${failure.code}`;
}
