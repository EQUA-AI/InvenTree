import axios from 'axios';

/**
 * The harness's stand-in for the application's ApiContext: a bare axios
 * instance, so every request the panel makes goes to the page's own origin
 * where Playwright intercepts it.
 */
export const api = axios.create();

export function useApi() {
  return api;
}

export default api;
