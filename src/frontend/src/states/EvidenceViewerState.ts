import { create } from 'zustand';
import type { MediaPreviewItem } from '../components/aichat/evidenceLocator';
import { useAIChatState } from './AIChatState';
import { voiceController } from './VoiceSessionState';

let citationTrigger: HTMLElement | null = null;
let citationGeneration: number | null = null;
let returnSequence = 0;

export const useEvidenceViewerState = create<{
  item: MediaPreviewItem | null;
  open: (item: MediaPreviewItem, trigger?: HTMLElement) => void;
  close: () => void;
}>((set) => ({
  item: null,
  open: (item, trigger) => {
    returnSequence++;
    if (!useEvidenceViewerState.getState().item) {
      citationGeneration = useAIChatState.getState().sessionGeneration;
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
    const generation = citationGeneration;
    const sequence = ++returnSequence;
    citationTrigger = null;
    citationGeneration = null;
    // Let the assistant's reactivated focus trap finish its mount autofocus
    // before restoring the specific citation (also on touch devices).
    requestAnimationFrame(() =>
      setTimeout(() => {
        if (
          useEvidenceViewerState.getState().item ||
          sequence !== returnSequence ||
          generation !== useAIChatState.getState().sessionGeneration
        )
          return;
        const target =
          trigger?.isConnected && !trigger.closest('[inert]')
            ? trigger
            : document.querySelector<HTMLElement>(
                '[aria-controls="ai-chat-drawer"]'
              );
        target?.focus({ preventScroll: true });
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
