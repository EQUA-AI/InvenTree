import { create } from 'zustand';
import { VoiceSessionController } from '../components/ai/voice/VoiceSessionController';
import {
  type VoiceSnapshot,
  initialVoiceSnapshot
} from '../components/ai/voice/types';
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
  requestStart: () => set({ consent: true }),
  closeConsent: () => set({ consent: false })
}));
// Account boundaries, unlike component unmounts, always release media.
useUserState.subscribe((state, previous) => {
  if (
    state.user?.pk !== previous.user?.pk ||
    (previous.is_authed && !state.is_authed)
  ) {
    voiceController.logout();
    useLocalState.getState().setAllowMobile(false);
    useVoiceSurfaceState.setState({ fullscreen: false, consent: false });
  }
});
