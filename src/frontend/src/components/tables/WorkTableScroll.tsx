import { Table, type TableScrollContainerProps } from '@mantine/core';
import { type FocusEvent, useLayoutEffect, useState } from 'react';

/** A scroll viewport is a keyboard stop only while it has undisclosed content. */
export function useWorkTableScroll(label: string) {
  const [viewport, setViewport] = useState<HTMLDivElement | null>(null);
  const [overflow, setOverflow] = useState(false);
  useLayoutEffect(() => {
    if (!viewport) return;
    const measure = () => {
      viewport.style.setProperty(
        '--work-scroll-width',
        `${viewport.clientWidth}px`
      );
      setOverflow(
        viewport.scrollWidth - viewport.clientWidth > 1 ||
          viewport.scrollHeight - viewport.clientHeight > 1
      );
    };
    const resize = new ResizeObserver(measure);
    const observe = () => {
      resize.disconnect();
      resize.observe(viewport);
      if (viewport.firstElementChild)
        resize.observe(viewport.firstElementChild);
      for (const table of viewport.querySelectorAll('table'))
        resize.observe(table);
      measure();
    };
    const content = new MutationObserver(observe);
    content.observe(viewport, { childList: true, subtree: true });
    observe();
    return () => {
      content.disconnect();
      resize.disconnect();
    };
  }, [viewport]);
  return {
    viewportRef: setViewport,
    viewportProps: {
      tabIndex: overflow ? 0 : -1,
      role: 'group',
      'aria-label': label,
      'data-work-scroll-viewport': true,
      onFocusCapture: (event: FocusEvent<HTMLDivElement>) => {
        if (
          event.target === event.currentTarget ||
          !event.target.matches(':focus-visible')
        )
          return;
        // Native Tab scrolling may accept a partially visible link, including
        // wrapped text clipped by this viewport. Explicitly reveal its box on
        // both axes; Work's measured dock margin applies to the outer scroll.
        event.target.scrollIntoView({
          block: 'nearest',
          inline: 'center',
          behavior: 'instant'
        });
      }
    }
  };
}

export function WorkTableScroll({
  label,
  ...props
}: TableScrollContainerProps & { label: string }) {
  const scrollAreaProps = useWorkTableScroll(label);
  return <Table.ScrollContainer {...props} scrollAreaProps={scrollAreaProps} />;
}
