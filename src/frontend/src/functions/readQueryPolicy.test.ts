import { AxiosError } from 'axios';
import { expect, it } from 'vitest';
import {
  InvalidReadResponse,
  canRefreshRead,
  readFailure,
  readPollInterval,
  readRetryDelay,
  retryRead
} from './readQueryPolicy';

function failure(status: number, detail = '', retryAfter?: string) {
  return new AxiosError('request failed', undefined, undefined, undefined, {
    status,
    data: { detail },
    headers: { 'retry-after': retryAfter }
  } as any);
}

it('distinguishes expired sessions, denied scope, and unsupported collection routes', () => {
  expect(
    readFailure(failure(403, 'Authentication credentials were not provided.'))
  ).toBe('authentication');
  expect(readFailure(failure(403, 'scope unresolved'))).toBe('forbidden');
  for (const status of [401, 403, 404, 405, 400, 422]) {
    const error = failure(status);
    const query = {
      state: { error, errorUpdatedAt: Date.now(), fetchFailureCount: 1 }
    };
    expect(retryRead(0, error)).toBe(false);
    expect(readPollInterval(query, 5000)).toBe(false);
    expect(canRefreshRead(query)).toBe(false);
  }
  expect(retryRead(0, new InvalidReadResponse('HTML instead of JSON'))).toBe(
    false
  );
});

it('backs off temporary errors, bounds retries, and respects Retry-After', () => {
  const error = failure(429, '', '120');
  expect(retryRead(0, error)).toBe(true);
  expect(retryRead(2, error)).toBe(false);
  expect(readRetryDelay(0, error)).toBe(120_000);
  const query = {
    state: { error, errorUpdatedAt: Date.now(), fetchFailureCount: 3 }
  };
  expect(readPollInterval(query, 5000)).toBeGreaterThan(119_000);
  expect(canRefreshRead(query)).toBe(false);
  expect(
    readRetryDelay(
      0,
      failure(503, '', new Date(Date.now() + 60_000).toUTCString())
    )
  ).toBeGreaterThan(58_000);
  expect(
    readPollInterval(
      { state: { error: null, errorUpdatedAt: 0, fetchFailureCount: 0 } },
      5000
    )
  ).toBe(5000);
});
