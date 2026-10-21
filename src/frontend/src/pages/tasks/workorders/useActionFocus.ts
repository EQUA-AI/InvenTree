import { useEffect, useLayoutEffect, useRef } from 'react';

/** Recover once for this command's pending transition, never a later refresh. */
export function useActionFocus(pending: boolean) {
  const ref = useRef<HTMLDivElement>(null);
  const action = useRef<{ node: HTMLElement; pendingSeen: boolean } | null>(
    null
  );
  const frame = useRef<number | undefined>(undefined);
  useEffect(() => () => cancelAnimationFrame(frame.current ?? 0), []);
  useLayoutEffect(() => {
    const operation = action.current;
    if (!operation) return;
    if (pending) operation.pendingSeen = true;
    const previous = operation.node;
    if (
      operation.pendingSeen &&
      (!previous.isConnected || previous.matches(':disabled'))
    ) {
      if (
        document.activeElement === document.body ||
        document.activeElement === previous
      )
        ref.current?.focus({ preventScroll: true });
      action.current = null;
    } else if (!pending) {
      action.current = null;
    }
  }, [pending]);
  function run<T>(command: () => T): T {
    const active = document.activeElement;
    const operation =
      active instanceof HTMLElement && ref.current?.contains(active)
        ? { node: active, pendingSeen: false }
        : null;
    action.current = operation;
    cancelAnimationFrame(frame.current ?? 0);
    const finish = () => {
      frame.current = requestAnimationFrame(() => {
        if (action.current === operation && !operation?.pendingSeen) {
          const previous = operation?.node;
          if (
            previous &&
            (!previous.isConnected || previous.matches(':disabled')) &&
            (document.activeElement === document.body ||
              document.activeElement === previous)
          )
            ref.current?.focus({ preventScroll: true });
          action.current = null;
        }
      });
    };
    const result = command();
    // Lock acquisition may precede the pending render. Keep only this operation
    // armed until its returned promise settles, then retire it unconditionally.
    if (result instanceof Promise) void result.then(finish, finish);
    else finish();
    return result;
  }
  return { props: { ref, tabIndex: -1 as const, role: 'group' }, run };
}
