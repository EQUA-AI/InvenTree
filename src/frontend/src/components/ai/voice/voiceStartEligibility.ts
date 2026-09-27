/**
 * One definition of voice-start eligibility (plan C3), shared by the
 * composer mic and the keyboard shortcut path.
 *
 * Starting a session must not bypass shared-thread/read-only state,
 * thread deletion, an in-flight scope application, or an in-flight typed
 * turn. Ending/muting an EXISTING session is never blocked here.
 */

export type VoiceStartBlockReason =
  | 'read-only'
  | 'deletion'
  | 'applying-scope'
  | 'turn-in-flight'
  | 'syncing';

export function voiceStartBlockReason(state: {
  sharedThread?: boolean;
  deletionPending?: boolean;
  applyingScope?: boolean;
  turnInFlight?: boolean;
  syncing?: boolean;
}): VoiceStartBlockReason | null {
  if (state.sharedThread) return 'read-only';
  if (state.deletionPending) return 'deletion';
  if (state.applyingScope) return 'applying-scope';
  if (state.turnInFlight) return 'turn-in-flight';
  if (state.syncing) return 'syncing';
  return null;
}
