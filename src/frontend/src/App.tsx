import { QueryClient } from '@tanstack/react-query';
import axios, { type AxiosError } from 'axios';

import { ApiEndpoints, apiUrl } from '@lib/index';
import { frontendID, serviceName } from './defaults/defaults';
import { useLocalState } from './states/LocalState';

// Global API instance
export const api = axios.create({});

/*
 * Setup default settings for the Axios API instance.
 */
export function setApiDefaults() {
  const { getHost } = useLocalState.getState();

  api.defaults.baseURL = getHost();
  api.defaults.timeout = 5000;

  api.defaults.withCredentials = true;
  api.defaults.withXSRFToken = true;
  api.defaults.xsrfCookieName = 'csrftoken';
  api.defaults.xsrfHeaderName = 'X-CSRFToken';

  axios.defaults.withCredentials = true;
  axios.defaults.withXSRFToken = true;
  axios.defaults.xsrfHeaderName = 'X-CSRFToken';
  axios.defaults.xsrfCookieName = 'csrftoken';
}

/**
 * Whether a failed query is worth attempting again.
 *
 * React Query's default is three retries for *any* failure, which is the wrong
 * shape for this app in two ways. A definitive answer from the server - a 400,
 * a 403, a 404 - will be identical next time, so retrying only multiplies load
 * and delays the error the user needs to see. And a page holding dozens of
 * queries turns one bad minute into hundreds of requests: the machine health
 * panel draws a sparkline per mapped signal, so a slow historian meant sixty-odd
 * queries each retrying three times against a source already struggling.
 *
 * Timeouts and rate limits are the exceptions worth another attempt, since both
 * say "not now" rather than "no".
 */
function shouldRetry(failureCount: number, error: unknown): boolean {
  const status = (error as AxiosError)?.response?.status;
  const definitive =
    status !== undefined &&
    status >= 400 &&
    status < 500 &&
    status !== 408 && // request timeout
    status !== 429; // rate limited
  return definitive ? false : failureCount < 2;
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: shouldRetry,
      // Back off rather than hammering: 1s, 2s, capped. A struggling backend
      // recovers faster when its callers wait.
      retryDelay: (attempt: number) => Math.min(1000 * 2 ** attempt, 15_000)
    },
    mutations: {
      // A write is not safe to repeat blindly; the caller decides.
      retry: false
    }
  }
});
export function setTraceId() {
  // check if we are in a secure context (https) - if not use of crypto is not allowed
  if (!window.isSecureContext) {
    return '';
  }

  const runID = crypto.randomUUID().replace(/-/g, '');
  const traceid = `00-${runID}-${frontendID}-01`;
  api.defaults.headers['traceparent'] = traceid;

  return runID;
}
export function removeTraceId(traceid: string) {
  delete api.defaults.headers['traceparent'];

  api
    .post(apiUrl(ApiEndpoints.system_internal_trace_end), {
      traceid: traceid,
      service: serviceName
    })
    .catch((error) => {
      console.error('Error removing trace ID:', error);
    });
}
