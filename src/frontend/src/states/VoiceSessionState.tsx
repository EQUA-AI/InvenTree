import { create } from 'zustand';
import { VoiceSessionController } from '../components/ai/voice/VoiceSessionController';
import {
  type VoiceSnapshot,
  initialVoiceSnapshot
} from '../components/ai/voice/types';
import { useAIChatState } from './AIChatState';
import { useLocalState } from './LocalState';
import { useUserState } from './UserState';
import { useVoiceDecisionState } from './VoiceDecisionState';

// Deliberately NOT persisted: no session credentials, transcript or decision in localStorage.
export const useVoiceSessionState = create<VoiceSnapshot>(
  () => initialVoiceSnapshot
);
export const voiceController = new VoiceSessionController(
  useVoiceSessionState,
  useVoiceDecisionState,
  () => useLocalState.getState(),
  (values) => useLocalState.getState().setVoicePreferences(values)
);
export const useVoiceSurfaceState = create<{
  fullscreen: boolean;
  consent: boolean;
  openFullscreen: () => void;
  closeFullscreen: () => void;
  requestStart: () => void;
  closeConsent: () => void;
}>((set) => ({
  fullscreen: false,
  consent: false,
  openFullscreen: () => set({ fullscreen: true }),
  closeFullscreen: () => set({ fullscreen: false }),
  requestStart: () => {
    const capability = useVoiceSessionState.getState().capability;
    if (capability?.enabled && capability.runtime?.available !== false)
      set({ consent: true });
  },
  closeConsent: () => set({ consent: false })
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
  useVoiceSurfaceState.setState({ fullscreen: false, consent: false });
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
