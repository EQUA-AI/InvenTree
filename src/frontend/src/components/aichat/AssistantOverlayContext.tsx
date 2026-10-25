import {
  type ReactNode,
  createContext,
  useContext,
  useId,
  useLayoutEffect,
  useMemo
} from 'react';
import { create } from 'zustand';

type AssistantOverlayState = {
  topOverlay: string | null;
  register: (id: string) => () => void;
};

const AssistantOverlayContext = createContext<AssistantOverlayState | null>(
  null
);

// One transient stack also covers global evidence, consent and hands-free
// portals. A local provider cannot coordinate a sibling portal on its own.
const useOverlayStack = create<{
  ids: string[];
  register: (id: string) => () => void;
}>((set) => ({
  ids: [],
  register: (id) => {
    set((state) => ({
      ids: [...state.ids.filter((value) => value !== id), id]
    }));
    return () =>
      set((state) => ({ ids: state.ids.filter((value) => value !== id) }));
  }
}));

/** Local assistant overlays relinquish ownership to existing global surfaces. */
export function AssistantOverlayProvider({
  children,
  suspended = false
}: {
  children: ReactNode;
  suspended?: boolean;
}) {
  const top = useOverlayStack((state) => state.ids.at(-1) ?? null);
  const register = useOverlayStack((state) => state.register);
  const value = useMemo(
    () => ({
      topOverlay: suspended ? null : top,
      register
    }),
    [top, register, suspended]
  );

  return (
    <AssistantOverlayContext.Provider value={value}>
      {children}
    </AssistantOverlayContext.Provider>
  );
}

export function useAssistantOverlayState() {
  return useContext(AssistantOverlayContext);
}

/** Register before paint; closing reactivates the parent before focus returns. */
export function useAssistantOverlay(opened: boolean): boolean {
  const id = useId();
  const context = useAssistantOverlayState();
  const register = useOverlayStack((state) => state.register);
  const top = useOverlayStack((state) => state.ids.at(-1));

  useLayoutEffect(() => {
    if (opened) return register(id);
  }, [id, opened, register]);

  // A standalone picker retains Mantine's own modal behavior.
  return opened && top === id && (!context || context.topOverlay === id);
}

export function useAssistantOverlayDepth() {
  return useOverlayStack((state) => state.ids.length);
}
