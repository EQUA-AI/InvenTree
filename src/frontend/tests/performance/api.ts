import { QueryClient } from '@tanstack/react-query';
import axios from 'axios';

/**
 * The harness's stand-in for the application's ApiContext: a bare axios
 * instance, so every request the panel makes goes to the page's own origin
 * where Playwright intercepts it.
 */
export const api = axios.create();
export const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } }
});

/** This component harness only serves fixture requests on its own origin. */
export function setApiDefaults() {
  api.defaults.baseURL = window.location.origin;
  api.defaults.timeout = 5000;
  api.defaults.withCredentials = true;
  api.defaults.withXSRFToken = true;
  api.defaults.xsrfCookieName = 'csrftoken';
  api.defaults.xsrfHeaderName = 'X-CSRFToken';
}

export function useApi() {
  return api;
}

export default api;
