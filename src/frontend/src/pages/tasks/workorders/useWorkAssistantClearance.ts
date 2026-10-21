import { useEffect, useRef } from 'react';
import { useAIChatState } from '../../../states/AIChatState';

/** Preserve usable Work width; resizing writes CSS rather than rendering Work. */
export function useWorkAssistantClearance(isPhone: boolean) {
  const opened = useAIChatState((state) => state.isOpen);
  const surface = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = surface.current;
    if (!node) return;
    const reset = () => {
      node.style.removeProperty('--work-assistant-width');
      node.removeAttribute('data-assistant-layout');
    };
    if (!opened || isPhone) {
      reset();
      return;
    }
    let drawer: HTMLElement | null = null;
    const measure = () => {
      const width = drawer?.getBoundingClientRect().width ?? 0;
      const available = node.parentElement?.clientWidth ?? window.innerWidth;
      const reserve = width > 0 && available - width >= 600;
      node.style.setProperty(
        '--work-assistant-width',
        `${reserve ? Math.ceil(width) : 0}px`
      );
      node.setAttribute(
        'data-assistant-layout',
        width && !reserve ? 'overlay' : 'reserved'
      );
    };
    const resize = new ResizeObserver(measure);
    if (node.parentElement) resize.observe(node.parentElement);
    const attach = () => {
      drawer = document.getElementById('ai-chat-drawer');
      if (!drawer) return;
      resize.observe(drawer);
      appearance.disconnect();
      measure();
    };
    const appearance = new MutationObserver(attach);
    appearance.observe(document.body, { childList: true, subtree: true });
    attach();
    return () => {
      appearance.disconnect();
      resize.disconnect();
      reset();
    };
  }, [opened, isPhone]);
  return surface;
}
