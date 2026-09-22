import { isAxiosError } from 'axios';

export class InvalidReadResponse extends Error {}

/** Collection reads only: an object-level 404 can have different semantics. */
export function readFailure(error: unknown) {
  if (!error) return null;
  if (error instanceof InvalidReadResponse) return 'invalid';
  const status = isAxiosError(error)
    ? error.response?.status
    : (error as { status?: number }).status;
  const detail = isAxiosError(error) ? error.response?.data?.detail : null;
  if (
    status === 401 ||
    (status === 403 &&
      detail === 'Authentication credentials were not provided.')
  )
    return 'authentication';
  if (status === 403) return 'forbidden';
  if (status === 404 || status === 405) return 'unsupported';
  if (status === 400 || status === 422) return 'invalid';
  return 'temporary';
}

export function retryAfterMs(error: unknown, now = Date.now()): number {
  const value = isAxiosError(error)
    ? error.response?.headers?.['retry-after']
    : (error as { retryAfter?: string | null } | null)?.retryAfter;
  if (!value) return 0;
  const seconds = Number(value);
  const delay = Number.isFinite(seconds)
    ? seconds * 1000
    : Date.parse(String(value)) - now;
  return Number.isFinite(delay) ? Math.max(0, delay) : 0;
}

export function retryRead(failures: number, error: unknown) {
  return readFailure(error) === 'temporary' && failures < 2;
}

export function readRetryDelay(attempt: number, error: unknown) {
  return Math.max(retryAfterMs(error), Math.min(30_000, 1000 * 2 ** attempt));
}

type ReadQuery = {
  state: { error: unknown; errorUpdatedAt: number; fetchFailureCount: number };
};

export function canRefreshRead(query: ReadQuery) {
  const { error, errorUpdatedAt } = query.state;
  return (
    (!error || readFailure(error) === 'temporary') &&
    Date.now() >= errorUpdatedAt + retryAfterMs(error, errorUpdatedAt)
  );
}

export function readPollInterval(
  query: ReadQuery,
  interval: number
): number | false {
  const { error, errorUpdatedAt, fetchFailureCount } = query.state;
  if (error && readFailure(error) !== 'temporary') return false;
  return Math.max(
    interval * Math.min(8, 2 ** fetchFailureCount),
    errorUpdatedAt + retryAfterMs(error, errorUpdatedAt) - Date.now()
  );
}

export const readQueryPolicy = {
  retry: retryRead,
  retryDelay: readRetryDelay,
  refetchOnWindowFocus: canRefreshRead,
  refetchOnReconnect: canRefreshRead,
  refetchOnMount: canRefreshRead
};
