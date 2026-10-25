import { useLocalStorage } from '@mantine/hooks';
import {
  type KeyboardEvent,
  type PointerEvent,
  useEffect,
  useRef,
  useState
} from 'react';

export const ASSISTANT_MIN_WIDTH = 340;
export const ASSISTANT_MAX_WIDTH = 900;
export function assistantGeometry(
  preference: number,
  viewport: number,
  expanded: boolean
) {
  const width = Math.max(0, Number.isFinite(viewport) ? viewport : 0);
  const modal = expanded || width <= 960;
  const maximum = Math.max(
    ASSISTANT_MIN_WIDTH,
    Math.min(ASSISTANT_MAX_WIDTH, width - 320)
  );
  const stored = Number.isFinite(preference) ? preference : 560;
  return {
    modal,
    maximum,
    width: modal
      ? width
      : Math.min(maximum, Math.max(ASSISTANT_MIN_WIDTH, stored))
  };
}
function viewport() {
  return {
    width: Math.min(
      window.innerWidth,
      window.visualViewport?.width ?? window.innerWidth
    ),
    height: window.visualViewport?.height ?? window.innerHeight,
    top: window.visualViewport?.offsetTop ?? 0,
    left: window.visualViewport?.offsetLeft ?? 0
  };
}
export function useAssistantGeometry(opened: boolean) {
  const [size, setSize] = useState(viewport);
  const [preference, setPreference] = useLocalStorage<number>({
    key: 'ai-chat-drawer-width',
    defaultValue: 560
  });
  const [expanded, setExpanded] = useState(false);
  const geometry = assistantGeometry(preference, size.width, expanded);
  const drag = useRef<{
    x: number;
    width: number;
    cursor: string;
    selection: string;
  } | null>(null);
  const stop = () => {
    const previous = drag.current;
    if (!previous) return;
    drag.current = null;
    document.body.style.cursor = previous.cursor;
    document.body.style.userSelect = previous.selection;
  };
  useEffect(() => {
    const resize = () => setSize(viewport());
    window.addEventListener('resize', resize);
    window.visualViewport?.addEventListener('resize', resize);
    window.visualViewport?.addEventListener('scroll', resize);
    return () => {
      window.removeEventListener('resize', resize);
      window.visualViewport?.removeEventListener('resize', resize);
      window.visualViewport?.removeEventListener('scroll', resize);
    };
  }, []);
  useEffect(() => {
    if (!opened || geometry.modal) stop();
  }, [opened, geometry.modal]);
  useEffect(() => {
    const move = (event: globalThis.PointerEvent) => {
      if (!drag.current) return;
      setPreference(
        Math.max(
          ASSISTANT_MIN_WIDTH,
          Math.min(
            geometry.maximum,
            drag.current.width + drag.current.x - event.clientX
          )
        )
      );
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', stop);
    document.addEventListener('pointercancel', stop);
    window.addEventListener('blur', stop);
    return () => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', stop);
      document.removeEventListener('pointercancel', stop);
      window.removeEventListener('blur', stop);
      stop();
    };
  }, [geometry.maximum, setPreference]);
  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (geometry.modal || event.button !== 0) return;
    event.preventDefault();
    drag.current = {
      x: event.clientX,
      width: geometry.width,
      cursor: document.body.style.cursor,
      selection: document.body.style.userSelect
    };
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const next =
      event.key === 'ArrowLeft'
        ? geometry.width + 24
        : event.key === 'ArrowRight'
          ? geometry.width - 24
          : event.key === 'Home'
            ? ASSISTANT_MIN_WIDTH
            : event.key === 'End'
              ? geometry.maximum
              : null;
    if (next === null) return;
    event.preventDefault();
    setPreference(
      Math.min(geometry.maximum, Math.max(ASSISTANT_MIN_WIDTH, next))
    );
  };
  return {
    ...geometry,
    narrow: size.width <= 960,
    expanded,
    toggleExpanded: () => setExpanded((value) => !value),
    viewportHeight: size.height,
    viewportTop: size.top,
    viewportLeft: size.left + size.width - geometry.width,
    onPointerDown,
    onKeyDown
  };
}
