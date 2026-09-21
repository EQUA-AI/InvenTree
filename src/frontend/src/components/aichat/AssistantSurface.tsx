import { Box, FocusTrap, Overlay, Paper, Portal } from '@mantine/core';
import { type ReactNode, useEffect, useRef, useState } from 'react';
import { isolateDialog } from './dialogBackground';

/** Persistent desktop side region, becoming a single dialog on small screens. */
export function AssistantSurface({
  opened,
  modal,
  suspended,
  width,
  title,
  onClose,
  children
}: {
  opened: boolean;
  modal: boolean;
  suspended: boolean;
  width: number;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const trigger = useRef<HTMLElement | null>(null);
  const [surface, setSurface] = useState<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!opened || !modal || suspended || !surface) return;
    const restoreBackground = isolateDialog(surface);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      restoreBackground();
      document.body.style.overflow = overflow;
    };
  }, [opened, modal, suspended, surface]);
  useEffect(() => {
    if (!opened) return;
    trigger.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    return () => {
      trigger.current?.focus();
    };
  }, [opened]);
  return (
    <Portal>
      <div ref={setSurface}>
        {opened && modal && !suspended && (
          <Overlay fixed zIndex={199} onClick={onClose} />
        )}
        <FocusTrap active={opened && modal && !suspended}>
          <Paper
            component='aside'
            id='ai-chat-drawer'
            data-testid='ai-chat-drawer'
            role={modal ? 'dialog' : 'complementary'}
            aria-modal={modal && !suspended ? true : undefined}
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
              width: modal ? '100%' : width,
              maxWidth: '100vw',
              height: '100dvh',
              zIndex: 200,
              overflow: 'hidden',
              background: 'var(--mantine-color-body)'
            }}
          >
            <Box
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
