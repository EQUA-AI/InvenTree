import { Box, FocusTrap, Overlay, Paper, Portal } from '@mantine/core';
import {
  type ReactNode,
  useEffect,
  useLayoutEffect,
  useRef,
  useState
} from 'react';
import { useAIChatState } from '../../states/AIChatState';
import {
  AssistantOverlayProvider,
  useAssistantOverlayState
} from './AssistantOverlayContext';
import { isolateDialog } from './dialogBackground';

type AssistantSurfaceProps = {
  opened: boolean;
  returnFocusTarget?: HTMLElement | null;
  modal: boolean;
  suspended: boolean;
  width: number;
  height?: number;
  top?: number;
  left?: number;
  title: string;
  onClose: () => void;
  children: ReactNode;
};

/** Persistent desktop side region, becoming a single dialog on small screens. */
export function AssistantSurface(props: AssistantSurfaceProps) {
  return (
    <AssistantOverlayProvider suspended={props.suspended}>
      <AssistantSurfaceFrame {...props} />
    </AssistantOverlayProvider>
  );
}

function AssistantSurfaceFrame({
  opened,
  returnFocusTarget,
  modal,
  suspended,
  width,
  height,
  top = 0,
  left,
  title,
  onClose,
  children
}: AssistantSurfaceProps) {
  const overlayState = useAssistantOverlayState();
  const inactive = suspended || overlayState?.topOverlay != null;
  const trigger = useRef<HTMLElement | null>(null);
  const [surface, setSurface] = useState<HTMLDivElement | null>(null);
  const [overlay, setOverlay] = useState(false);
  const dialog = modal || overlay;
  // Capture the launcher before modal isolation blurs the background and
  // before the native focus trap moves focus into the composer.
  useLayoutEffect(() => {
    if (opened)
      trigger.current =
        returnFocusTarget ??
        (document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null);
  }, [opened, returnFocusTarget]);
  useEffect(() => {
    if (!opened) {
      setOverlay(false);
      return;
    }
    const measure = () =>
      setOverlay(
        Boolean(document.querySelector('[data-assistant-layout="overlay"]'))
      );
    const observer = new MutationObserver(measure);
    observer.observe(document.body, {
      attributes: true,
      attributeFilter: ['data-assistant-layout'],
      childList: true,
      subtree: true
    });
    measure();
    return () => observer.disconnect();
  }, [opened]);
  useLayoutEffect(() => {
    if (!opened || !dialog || inactive || !surface) return;
    const restoreBackground = isolateDialog(surface);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      restoreBackground();
      document.body.style.overflow = overflow;
    };
  }, [opened, dialog, inactive, surface]);
  useEffect(() => {
    if (!opened) return;
    const generation = useAIChatState.getState().sessionGeneration;
    return () => {
      if (
        generation === useAIChatState.getState().sessionGeneration &&
        trigger.current?.isConnected &&
        !trigger.current.closest('[inert]')
      )
        trigger.current.focus({ preventScroll: true });
    };
  }, [opened]);
  return (
    <Portal>
      <div ref={setSurface}>
        {opened && dialog && !inactive && (
          <Overlay fixed zIndex={199} onClick={onClose} />
        )}
        <FocusTrap active={opened && dialog && !inactive}>
          <Paper
            component='aside'
            id='ai-chat-drawer'
            data-testid='ai-chat-drawer'
            role={dialog ? 'dialog' : 'complementary'}
            aria-modal={dialog && !inactive ? true : undefined}
            aria-label={title}
            inert={!opened || inactive}
            shadow='lg'
            onKeyDown={(event) => {
              if (
                event.key === 'Escape' &&
                !event.defaultPrevented &&
                !inactive &&
                event.target instanceof Node &&
                event.currentTarget.contains(event.target)
              ) {
                event.stopPropagation();
                onClose();
              }
            }}
            style={{
              display: opened ? 'flex' : 'none',
              flexDirection: 'column',
              position: 'fixed',
              top,
              left,
              right: left === undefined ? 0 : undefined,
              width,
              maxWidth: '100vw',
              height: height ?? '100dvh',
              // Resize/keyboard events update React geometry asynchronously.
              // Keep the composer inside the viewport before that update lands.
              maxHeight: '100dvh',
              zIndex: 200,
              overflow: 'hidden',
              background: 'var(--mantine-color-body)'
            }}
          >
            <Box
              data-testid='ai-chat-scroll-content'
              style={{
                display: 'flex',
                flexDirection: 'column',
                flex: 1,
                minHeight: 0
              }}
            >
              {children}
            </Box>
          </Paper>
        </FocusTrap>
      </div>
    </Portal>
  );
}
