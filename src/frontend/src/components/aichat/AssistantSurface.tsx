import { Box, FocusTrap, Overlay, Paper, Portal } from '@mantine/core';
import { useViewportSize } from '@mantine/hooks';
import {
  type ReactNode,
  useEffect,
  useLayoutEffect,
  useRef,
  useState
} from 'react';
import { isolateDialog } from './dialogBackground';

export function clampAssistantWidth(width: number, viewport: number) {
  return Math.min(
    900,
    Math.max(340, Number.isFinite(width) ? width : 440),
    Math.max(0, viewport - 32)
  );
}

/** Persistent desktop side region, becoming a single dialog on small screens. */
export function AssistantSurface({
  opened,
  returnFocusTarget,
  modal,
  suspended,
  width,
  title,
  resizeHandle,
  onClose,
  children
}: {
  opened: boolean;
  returnFocusTarget?: HTMLElement | null;
  modal: boolean;
  suspended: boolean;
  width: number;
  title: string;
  resizeHandle?: ReactNode;
  onClose: () => void;
  children: ReactNode;
}) {
  const trigger = useRef<HTMLElement | null>(null);
  const [surface, setSurface] = useState<HTMLDivElement | null>(null);
  const [overlay, setOverlay] = useState(false);
  const viewport = useViewportSize();
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
  useEffect(() => {
    if (!opened || !dialog || suspended || !surface) return;
    const restoreBackground = isolateDialog(surface);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      restoreBackground();
      document.body.style.overflow = overflow;
    };
  }, [opened, dialog, suspended, surface]);
  useEffect(() => {
    if (!opened) return;
    return () => {
      trigger.current?.focus();
    };
  }, [opened]);
  return (
    <Portal>
      <div ref={setSurface}>
        {opened && dialog && !suspended && (
          <Overlay fixed zIndex={199} onClick={onClose} />
        )}
        <FocusTrap active={opened && dialog && !suspended}>
          <Paper
            component='aside'
            id='ai-chat-drawer'
            data-testid='ai-chat-drawer'
            role={dialog ? 'dialog' : 'complementary'}
            aria-modal={dialog && !suspended ? true : undefined}
            aria-label={title}
            inert={!opened || suspended}
            shadow='lg'
            onKeyDown={(event) => {
              if (
                event.key === 'Escape' &&
                !event.defaultPrevented &&
                !suspended
              ) {
                event.stopPropagation();
                onClose();
              }
            }}
            style={{
              display: opened ? 'flex' : 'none',
              flexDirection: 'column',
              position: 'fixed',
              inset: '0 0 0 auto',
              width: modal
                ? '100%'
                : clampAssistantWidth(
                    width,
                    viewport.width || window.innerWidth
                  ),
              maxWidth: '100vw',
              height: '100dvh',
              zIndex: 200,
              overflow: 'hidden',
              background: 'var(--mantine-color-body)'
            }}
          >
            {resizeHandle}
            <Box
              data-testid='ai-chat-scroll-content'
              style={{
                display: 'flex',
                flexDirection: 'column',
                flex: 1,
                minHeight: 0,
                overflowY: 'auto',
                paddingInlineStart: 6
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
