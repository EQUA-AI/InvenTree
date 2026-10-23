import type { ComboboxProps } from '@mantine/core';
import { useCallback, useLayoutEffect, useReducer, useRef } from 'react';

const comboboxProps = {
  withinPortal: true,
  floatingStrategy: 'fixed',
  hideDetached: false,
  preventPositionChangeWhenVisible: false,
  middlewares: { flip: true, shift: { crossAxis: true, padding: 8 } }
} satisfies ComboboxProps;

/** Keep a comparison editor reachable while its portalled options are open. */
export function useComparisonDropdown() {
  const ref = useRef<HTMLInputElement>(null);
  const pending = useRef(false);
  const [edit, afterCommit] = useReducer((value: number) => value + 1, 0);
  const revealNow = useCallback(() => {
    const input = ref.current;
    if (!input || input !== document.activeElement) return;
    const bounds = input.getBoundingClientRect();
    // Leave room for pills and options without moving focus or smooth scrolling.
    const margin = window.innerWidth < 768 ? 160 : 16;
    if (bounds.top < 16 || bounds.bottom > window.innerHeight - margin)
      input.scrollIntoView({
        block: 'center',
        inline: 'nearest',
        behavior: 'instant'
      });
  }, []);
  const reveal = useCallback(() => {
    // Mantine calls open/search handlers before committing its dropdown. Scroll
    // now, then check new pill wrapping before paint, never in a later frame.
    revealNow();
    pending.current = true;
    afterCommit();
  }, [revealNow]);
  const cancel = useCallback(() => {
    pending.current = false;
  }, []);
  useLayoutEffect(() => {
    if (pending.current) {
      pending.current = false;
      revealNow();
    }
  }, [edit, revealNow]);
  return {
    ref,
    reveal,
    dropdownProps: {
      onDropdownOpen: reveal,
      onDropdownClose: cancel,
      comboboxProps,
      maxDropdownHeight: 240
    }
  };
}
