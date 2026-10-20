import { create } from 'zustand';
import { VoiceSessionController } from '../components/ai/voice/VoiceSessionController';
import {
  type VoiceSnapshot,
  initialVoiceSnapshot
} from '../components/ai/voice/types';
import {
  type VoiceStartFeedback,
  voiceStartFeedback
} from '../components/ai/voice/voiceFeedback';
import {
  type ContextPreflight,
  unregisteredPreflightOutcome
} from '../components/aichat/contextPreflight';
import { useAIChatState } from './AIChatState';
import { useLocalState } from './LocalState';
import { useThreadFocusState } from './ThreadFocusState';
import { useUserState } from './UserState';
import { useVoiceDecisionState } from './VoiceDecisionState';

// Deliberately NOT persisted: no session credentials, transcript or decision in localStorage.
export const useVoiceSessionState = create<VoiceSnapshot>(
  () => initialVoiceSnapshot
);

/**
 * Drawer-owned shared machine-context preflight (plan 3.1). Registration is
 * identity-checked so a StrictMode double-mount cleanup can never drop a
 * newer registration, and the fallback keeps failing closed on an
 * unconsumed machine hint when no drawer surface is mounted.
 */
let registeredContextPreflight: ContextPreflight | null = null;
export function registerContextPreflight(
  preflight: ContextPreflight
): () => void {
  registeredContextPreflight = preflight;
  return () => {
    if (registeredContextPreflight === preflight)
      registeredContextPreflight = null;
  };
}
const runContextPreflight: ContextPreflight = async (context) => {
  if (registeredContextPreflight) return registeredContextPreflight(context);
  // No drawer surface mounted: an unconsumed target hint (machine/task/work
  // order) still fails closed — it is never dropped into an unscoped entry.
  return unregisteredPreflightOutcome({
    hintPending: useAIChatState.getState().targetHint != null,
    threadId: context.threadId
  });
};
export const voiceController = new VoiceSessionController(
  useVoiceSessionState,
  useVoiceDecisionState,
  () => useLocalState.getState(),
  (values) => useLocalState.getState().setVoicePreferences(values),
  runContextPreflight,
  // Plan C: the stamp source is the server-confirmed thread focus version
  // the user currently sees (the Talking about chip state).
  () => useThreadFocusState.getState().version
);
export const useVoiceSurfaceState = create<{
  fullscreen: boolean;
  consent: boolean;
  openFullscreen: () => void;
  closeFullscreen: () => void;
  requestStart: () => void;
  closeConsent: () => void;
  /** VOICE-H05: why the last start request was refused (null = none). */
  startFeedback: VoiceStartFeedback | null;
  /**
   * Shared start eligibility (plan C3): while set, neither the composer mic
   * nor the keyboard shortcut may start a session (ending/muting an
   * existing session is never blocked).
   */
  startBlockedReason: string | null;
  setStartBlockedReason: (reason: string | null) => void;
}>((set, get) => ({
  fullscreen: false,
  consent: false,
  openFullscreen: () => set({ fullscreen: true }),
  closeFullscreen: () => set({ fullscreen: false }),
  requestStart: () => {
    // VOICE-H05: the proceed condition below is unchanged (eligibility and
    // capability gates intact); a refusal is now recorded so surfaces can
    // render an accessible explanation with a safe retry instead of a
    // silent no-op. No direct mic/controller start ever happens here.
    const capability = useVoiceSessionState.getState().capability;
    const feedback = voiceStartFeedback({
      startBlockedReason: get().startBlockedReason,
      capability: capability && {
        enabled: capability.enabled,
        runtime: capability.runtime && {
          available: capability.runtime.available,
          state: capability.runtime.state
        }
      }
    });
    if (feedback) {
      set({ startFeedback: feedback });
      return;
    }
    set({ consent: true, startFeedback: null });
  },
  closeConsent: () => set({ consent: false }),
  startBlockedReason: null,
  startFeedback: null,
  // Eligibility boundary: a reason change clears stale feedback.
  setStartBlockedReason: (reason) =>
    set({ startBlockedReason: reason, startFeedback: null })
}));
// Account boundaries, unlike component unmounts, always release media.
function clearVoiceBoundary() {
  const voice = useVoiceSessionState.getState();
  if (
    voice.transport !== 'off' ||
    voice.mic !== 'off' ||
    voice.playback !== 'idle' ||
    voice.session ||
    voice.revisions.length ||
    voice.lastSubmitted ||
    voice.lastSpoken ||
    voice.presentation
  ) {
    voiceController.logout();
  }
  voiceController.setCapability(null);
  useVoiceSurfaceState.setState({
    fullscreen: false,
    consent: false,
    startFeedback: null
  });
}
useAIChatState.subscribe((state, previous) => {
  if (state.sessionGeneration !== previous.sessionGeneration)
    clearVoiceBoundary();
});
useUserState.subscribe((state, previous) => {
  if (
    state.user?.pk !== previous.user?.pk ||
    (previous.is_authed && !state.is_authed)
  ) {
    clearVoiceBoundary();
    useLocalState.getState().setAllowMobile(false);
  }
});
