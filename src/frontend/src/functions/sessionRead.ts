import { isAxiosError, isCancel } from 'axios';

// Startup involves two sequential reads. Keep the whole restoration bounded,
// while allowing slower deployments more time than the general API timeout.
export const SESSION_CHECK_BUDGET_MS = 20_000;
const ATTEMPT_TIMEOUT_MS = 10_000;
const RETRY_DELAY_MS = 250;

/** Retry an idempotent session/profile read once, within a shared deadline. */
export async function readSession<T>(
  read: (timeout: number) => Promise<T>,
  current: () => boolean,
  deadline: number
): Promise<T> {
  for (let attempt = 0; attempt < 2; attempt++) {
    const remaining = deadline - performance.now();
    if (!current() || remaining <= 0) {
      throw new Error('Session check superseded or deadline exceeded');
    }
    try {
      // XMLHttpRequest truncates fractional milliseconds; zero disables its timeout.
      return await read(Math.min(ATTEMPT_TIMEOUT_MS, Math.ceil(remaining)));
    } catch (error) {
      const status = isAxiosError(error) ? error.response?.status : undefined;
      const transient =
        isAxiosError(error) &&
        !isCancel(error) &&
        (['ECONNABORTED', 'ETIMEDOUT', 'ERR_NETWORK'].includes(
          error.code ?? ''
        ) ||
          status === 408 ||
          (status !== undefined && status >= 500 && status <= 599));
      if (
        attempt === 1 ||
        !transient ||
        !current() ||
        deadline - performance.now() <= RETRY_DELAY_MS
      ) {
        throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS));
    }
  }
  throw new Error('Session check failed');
}
