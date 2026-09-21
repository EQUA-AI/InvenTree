import { useMediaQuery } from '@mantine/hooks';
import type { MouseEvent } from 'react';
import { useAIChatState } from '../../states/AIChatState';
import { voiceController } from '../../states/VoiceSessionState';

/** Preserve browser link semantics; uncover the destination on phones. */
export function useAssistantNavigation() {
  const narrow = useMediaQuery('(max-width: 48em)');
  return (event: MouseEvent<HTMLAnchorElement>) => {
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.ctrlKey ||
      event.metaKey ||
      event.shiftKey ||
      event.altKey
    )
      return;
    if (narrow) {
      void voiceController.end();
      useAIChatState.getState().close();
    }
  };
}
