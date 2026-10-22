import { useCallback, useEffect, useRef } from 'react';

/** Keep a comparison editor reachable while its portalled options are open. */
export function useComparisonDropdown() {
  const ref = useRef<HTMLInputElement>(null);
  const frame = useRef<number | null>(null);
  const cancel = useCallback(() => {
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
  }, []);
  const reveal = useCallback(() => {
    cancel();
    frame.current = requestAnimationFrame(() => {
      frame.current = null;
      const input = ref.current;
      if (!input) return;
      const bounds = input.getBoundingClientRect();
      // Leave room for pills and the options on phones, without moving focus.
      const margin = window.innerWidth < 768 ? 160 : 16;
      if (bounds.top < 16 || bounds.bottom > window.innerHeight - margin)
        input.scrollIntoView({
          block: 'center',
          inline: 'nearest',
          behavior: 'instant'
        });
    });
  }, [cancel]);
  useEffect(() => cancel, [cancel]);
  return { ref, reveal, cancel };
}
