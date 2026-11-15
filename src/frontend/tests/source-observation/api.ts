/** Browser fixture only: unused API paths cannot reach an external service. */
import { QueryClient } from '@tanstack/react-query';
import axios from 'axios';

export const queryClient = new QueryClient();
export const api = axios.create({
  adapter: async () => {
    throw new Error('No API source is installed in this fixture');
  }
});
export const setApiDefaults = () => {};
export const setTraceId = () => {};
export const removeTraceId = () => {};
