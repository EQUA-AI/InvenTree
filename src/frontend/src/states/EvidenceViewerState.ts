import { create } from 'zustand';
import type { MediaEvidenceItem } from '../hooks/UseAIChat';
import { useAIChatState } from './AIChatState';
import { voiceController } from './VoiceSessionState';

let citationTrigger: HTMLElement | null = null;

export const useEvidenceViewerState = create<{
  item: MediaEvidenceItem | null;
  open: (item: MediaEvidenceItem, trigger?: HTMLElement) => void;
  close: () => void;
}>((set) => ({
  item: null,
  open: (item, trigger) => {
    if (!useEvidenceViewerState.getState().item) {
      citationTrigger =
        trigger ??
        (document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null);
    }
    void voiceController.end();
    set({ item });
  },
  close: () => {
    set({ item: null });
    const trigger = citationTrigger;
    citationTrigger = null;
    // Let the assistant's reactivated focus trap finish its mount autofocus
    // before restoring the specific citation (also on touch devices).
    requestAnimationFrame(() =>
      setTimeout(() => {
        if (useEvidenceViewerState.getState().item) return;
        const target =
          trigger?.isConnected && !trigger.closest('[inert]')
            ? trigger
            : document.querySelector<HTMLElement>(
                '[aria-controls="ai-chat-drawer"]'
              );
        target?.focus();
      }, 0)
    );
  }
}));

useAIChatState.subscribe((state, previous) => {
  if (state.sessionGeneration !== previous.sessionGeneration) {
    citationTrigger = null;
    useEvidenceViewerState.getState().close();
  }
});
