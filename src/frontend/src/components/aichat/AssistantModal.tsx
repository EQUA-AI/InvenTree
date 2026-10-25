import { Modal, type ModalProps } from '@mantine/core';
import { useLayoutEffect, useRef, useState } from 'react';
import { useAIChatState } from '../../states/AIChatState';
import { useThreadFocusState } from '../../states/ThreadFocusState';
import {
  useAssistantOverlay,
  useAssistantOverlayDepth
} from './AssistantOverlayContext';
import workspaceStyles from './AssistantWorkspace.module.css';
import { isolateDialog } from './dialogBackground';

/** All assistant portals share focus/isolation ownership, including globals. */
export function AssistantModal({
  opened,
  onClose,
  returnFocus = true,
  trapFocus = true,
  closeOnEscape = true,
  closeOnClickOutside = true,
  ...props
}: ModalProps) {
  const top = useAssistantOverlay(opened);
  const depth = useAssistantOverlayDepth();
  const [element, setElement] = useState<HTMLDivElement | null>(null);
  const wasOpen = useRef(false);
  const trigger = useRef<{
    element: HTMLElement | null;
    generation: number;
    binding: ReturnType<typeof useThreadFocusState.getState>['binding'];
  } | null>(null);
  if (opened && !wasOpen.current) {
    trigger.current = {
      element:
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null,
      generation: useAIChatState.getState().sessionGeneration,
      binding: useThreadFocusState.getState().binding
    };
  }
  wasOpen.current = opened;

  useLayoutEffect(() => {
    if (!opened || !top || !element) return;
    const root =
      element.closest<HTMLElement>('[data-assistant-modal]') ?? element;
    return isolateDialog(root);
  }, [opened, top, element]);

  useLayoutEffect(() => {
    if (!opened || !returnFocus) return;
    const captured = trigger.current;
    return () => {
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          if (
            !captured ||
            captured.generation !==
              useAIChatState.getState().sessionGeneration ||
            captured.binding !== useThreadFocusState.getState().binding
          )
            return;
          const previous = captured.element;
          const fallback = document.querySelector<HTMLElement>(
            '[data-testid="select-ai-chat-thread"]'
          );
          const target = previous?.isConnected ? previous : fallback;
          if (
            target &&
            !target.closest('[inert]') &&
            !target.matches(':disabled')
          ) {
            target.focus({ preventScroll: true });
          }
        })
      );
    };
  }, [opened, returnFocus]);

  return (
    <Modal
      {...props}
      className={[workspaceStyles.modal, props.className]
        .filter(Boolean)
        .join(' ')}
      closeButtonProps={{ size: 44, ...props.closeButtonProps }}
      ref={setElement}
      data-assistant-modal
      opened={opened}
      onClose={() => {
        if (top) onClose();
      }}
      trapFocus={top && trapFocus}
      returnFocus={false}
      closeOnEscape={top && closeOnEscape}
      closeOnClickOutside={top && closeOnClickOutside}
      inert={opened && !top}
      zIndex={top ? 3000 + depth : 3000}
    />
  );
}
