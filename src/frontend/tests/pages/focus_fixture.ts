import type { ThreadFocusPayload } from '../../lib/types/AimmsWire.generated';

/** The server's GET/PUT projection when a thread has never selected a focus. */
export const emptyThreadFocusPayload: ThreadFocusPayload = {
  focus: null,
  focus_version: 0,
  focus_unavailable: false
};
