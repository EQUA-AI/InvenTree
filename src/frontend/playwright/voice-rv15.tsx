/**
 * RV15 mounted fixture — the REAL AIChatDrawer (voice onTurnResult callback at
 * AIChatDrawer.tsx:1387-1443) under real MemoryRouter/Query/Api/II18n, with
 * wholly mocked auth (seeded UserState, no credentials, no backend).
 *
 * Account identity flips are REAL user-store updates (pk AND userId() source
 * contract together, with rerender) — the same contract as
 * voice-deferred-focus.tsx. Transport/API/WebRTC/audio are mocked by the
 * Playwright spec via tests/pages/voice_harness.ts; nothing here calls a
 * provider, device or external endpoint. MOCKED BROWSER RENDERING only.
 */
import './aichat-drawer-lingui';

import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { Button, MantineProvider, Stack } from '@mantine/core';
import '@mantine/core/styles.css';
import { Notifications } from '@mantine/notifications';
import '@mantine/notifications/styles.css';
import { useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';

import { api, queryClient } from '../src/App';
import { VoiceConsentDialog } from '../src/components/ai/voice/VoiceConsentDialog';
import { VoiceGlobalIndicator } from '../src/components/ai/voice/VoiceGlobalIndicator';
import { VoiceHandsFreeSurface } from '../src/components/ai/voice/VoiceHandsFreeSurface';
import { AIChatButton, AIChatDrawer } from '../src/components/nav/AIChatDrawer';
import { ApiProvider } from '../src/contexts/ApiContext';
import { useAIChatState } from '../src/states/AIChatState';
import { useThreadFocusState } from '../src/states/ThreadFocusState';
import { useUserState } from '../src/states/UserState';
import { voiceController } from '../src/states/VoiceSessionState';

// Wholly mocked auth: no login request, no credentials. userId() derives from
// the user record, so pk AND userId() flip together on account change.
useUserState.setState({
  is_authed: true,
  authStatus: 'authenticated',
  user: {
    pk: 11,
    username: 'RV15 mounted fixture',
    // Ordinary-user stub (no permission model exercised; this fixture makes
    // no auth claim — mocked auth only).
    is_superuser: false,
    userId: () => (window as any).__rv15UserId ?? 11
  } as any
});

function DrawerFixture() {
  const opened = useAIChatState((state) => state.isOpen);
  // Real mounted-ready marker: set after the first committed render.
  useEffect(() => {
    document.body.dataset.rv15Ready = '1';
  }, []);
  return (
    <Stack p='md' gap='xs'>
      <Button
        data-testid='rv15-close-drawer'
        onClick={() => useAIChatState.getState().close()}
      >
        Close drawer
      </Button>
      <Button
        data-testid='rv15-open-drawer'
        onClick={() => useAIChatState.getState().open()}
      >
        Open drawer
      </Button>
      <AIChatButton
        opened={opened}
        onClick={() => {
          const state = useAIChatState.getState();
          if (opened) state.close();
          else state.open();
        }}
      />
      <AIChatDrawer
        opened={opened}
        onClose={() => useAIChatState.getState().close()}
      />
      <VoiceGlobalIndicator />
      <VoiceConsentDialog />
      <VoiceHandsFreeSurface />
    </Stack>
  );
}

// TEST-ONLY callback-attribution seam (parent-authorized): observe REAL
// VoiceSessionController dispatches to registered listeners, and deliver a
// synthesized result DIRECTLY to the ACTUAL mounted listener object that the
// real useVoiceLiveSession registered (which forwards to the real AIChatDrawer
// onTurnResult React callback). No hook is mocked or recreated, no production
// gate is disabled. The observer listener is EXCLUDED from direct delivery so
// a seam delivery is never miscounted as a controller dispatch.
const RV15_OBSERVER = '__rv15ObserverListener';
const dispatchLog: (string | null)[] = [];
const observerListener: Record<string, unknown> = {
  onTurnResult: (turn: { session_id?: string | null }) => {
    dispatchLog.push(turn?.session_id ?? null);
  }
};
observerListener[RV15_OBSERVER] = true;
voiceController.listeners.add(observerListener as never);

// TEST-ONLY configure-boundary seam (parent-authorized, one-shot): intercept
// the NEXT real voiceController.configure call for an armed target thread and
// deliver a prepared result to the ACTUAL mounted listeners BEFORE forwarding
// to the ORIGINAL configure method (always forwarded in a finally block, so
// the genuine native configure — including its real session teardown — is
// never suppressed). Isolated callback-at-configure-boundary delivery, NOT
// provider/transport/native-controller dispatch: the observer listener stays
// excluded (dispatchLog is never incremented by it), no controller state is
// forged or replaced, and the hook/drawer/helper are untouched. This seam
// exercises the UNCHANGED bound-thread mismatch rejection (the drawer still
// shows the session's creation thread check failing); it is NOT the changed
// createdThreadId==null predicate.
type ConfigureBoundaryArm = {
  targetThread: string;
  turn: Record<string, unknown>;
};
let armedConfigureBoundary: ConfigureBoundaryArm | null = null;
const configureBoundaryWitness: Record<string, unknown>[] = [];
const nativeConfigure = voiceController.configure.bind(voiceController);
(voiceController as any).configure = (host: string, threadId?: string) => {
  const arm = armedConfigureBoundary;
  if (!arm || threadId !== arm.targetThread)
    return nativeConfigure(host, threadId);
  armedConfigureBoundary = null;
  let forwarded: unknown;
  try {
    const sessionBeforeDelivery =
      (voiceController as any).snapshot?.session ?? null;
    let delivered = 0;
    for (const listener of Array.from(voiceController.listeners)) {
      const entry = listener as Record<string, any>;
      if (entry[RV15_OBSERVER]) continue;
      if (typeof entry.onTurnResult === 'function') {
        delivered += 1;
        entry.onTurnResult(arm.turn);
      }
    }
    configureBoundaryWitness.push({
      targetThread: arm.targetThread,
      configureThreadId: threadId ?? null,
      sessionBeforeDelivery: sessionBeforeDelivery?.id ?? null,
      delivered
    });
  } finally {
    forwarded = nativeConfigure(host, threadId);
  }
  return forwarded;
};

// TEST-ONLY first-session-snapshot seam (parent-authorized, one-shot): observe
// the GENUINE native update() that adopts the first real session snapshot
// (VoiceSessionController.start -> this.update({ session: created })), forward
// the native update UNCHANGED, then synchronously deliver a prepared result to
// the ACTUAL mounted listeners (observer excluded). The delivery runs inside
// the same synchronous task as the native snapshot transition — AFTER the
// real store session is nonempty (so the real session id is genuine and
// matches the turn) and BEFORE the task can unwind to React, i.e. before the
// drawer's voice binding is FIRST captured on render (AIChatDrawer.tsx
// 1435-1443). No state is forged and no ref is reset: the session snapshot,
// store, and drawer binding are exactly what native code produced; the
// binding is simply genuinely uncaptured at that instant. This is the
// missing-binding (createdThreadId == null) reachability seam.
type FirstSnapshotArm = { turn: Record<string, unknown> };
let armedFirstSnapshot: FirstSnapshotArm | null = null;
const firstSnapshotWitness: Record<string, unknown>[] = [];
const nativeUpdate = (voiceController as any).update.bind(voiceController);
(voiceController as any).update = (change: Record<string, unknown>) => {
  const sessionBefore = (voiceController as any).snapshot?.session ?? null;
  // ALWAYS forwarded unchanged (same change object) — the genuine native
  // store write is never suppressed or altered.
  const result = nativeUpdate(change);
  const arm = armedFirstSnapshot;
  const sessionAfter = (voiceController as any).snapshot?.session ?? null;
  if (
    arm &&
    sessionBefore == null &&
    sessionAfter != null &&
    change &&
    change.session != null
  ) {
    armedFirstSnapshot = null; // ONE-SHOT
    let delivered = 0;
    for (const listener of Array.from(voiceController.listeners)) {
      const entry = listener as Record<string, any>;
      if (entry[RV15_OBSERVER]) continue;
      if (typeof entry.onTurnResult === 'function') {
        delivered += 1;
        entry.onTurnResult(arm.turn);
      }
    }
    firstSnapshotWitness.push({
      seam: 'first-session-snapshot',
      sessionBeforeUpdate: sessionBefore?.id ?? null,
      sessionAfterUpdate: sessionAfter?.id ?? null,
      turnSessionId: arm.turn.session_id ?? null,
      delivered
    });
  }
  return result;
};

// Fixture seams: drawer open/close and REAL user-store identity updates only —
// no fake voice callbacks, no invented hook fallbacks.
(window as any).__rv15 = {
  open: () => useAIChatState.getState().open(),
  close: () => useAIChatState.getState().close(),
  accountUserId: () => useUserState.getState().userId(),
  // Witness A: every result the REAL controller dispatched to registered
  // listeners (empty = the transport result never reached any callback).
  controllerDispatches: () => dispatchLog.slice(),
  // Witness B / isolation: deliver to the ACTUAL mounted listener/React
  // callback (returns how many real listeners received it).
  deliverTurnToMountedListeners: (turn: Record<string, unknown>) => {
    let delivered = 0;
    for (const listener of Array.from(voiceController.listeners)) {
      const entry = listener as Record<string, any>;
      if (entry[RV15_OBSERVER]) continue;
      if (typeof entry.onTurnResult === 'function') {
        delivered += 1;
        entry.onTurnResult(turn);
      }
    }
    return delivered;
  },
  // TEST-ONLY one-shot configure-boundary seam (see the wrapper above).
  armConfigureBoundary: (
    targetThread: string,
    turn: Record<string, unknown>
  ) => {
    armedConfigureBoundary = { targetThread, turn };
  },
  configureBoundaryWitness: () => configureBoundaryWitness.slice(),
  // TEST-ONLY one-shot first-session-snapshot seam (see the wrapper above).
  armFirstSessionSnapshot: (turn: Record<string, unknown>) => {
    armedFirstSnapshot = { turn };
  },
  firstSessionSnapshotWitness: () => firstSnapshotWitness.slice(),
  // Real controller state read (never forged): active session id or null.
  activeVoiceSessionId: () =>
    (voiceController as any).snapshot?.session?.id ?? null,
  // Real mounted ThreadFocusState snapshot (populated-focus witness).
  threadFocus: () => {
    try {
      return JSON.stringify(useThreadFocusState.getState());
    } catch {
      return '';
    }
  },
  flipAccount: () => {
    const next = ((window as any).__rv15UserId ?? 11) === 11 ? 22 : 11;
    (window as any).__rv15UserId = next;
    // A REAL user-store update (new user object, new pk) so the drawer
    // rerenders and crosses its account cache-key boundary. The whole
    // identity flips together (pk + userId()); a bare global or a
    // userId-only flip leaves the key unchanged.
    useUserState.setState((prev: any) => ({
      user: { ...prev.user, pk: next, userId: () => next }
    }));
  }
};

const initialRoute =
  new URLSearchParams(window.location.search).get('route') ??
  '/machines/index/';

createRoot(document.getElementById('root')!).render(
  <I18nProvider i18n={i18n}>
    <MantineProvider defaultColorScheme='auto'>
      {/* Honest failure feedback renders through showNotification — the
          fixture must mount its portal. */}
      <Notifications autoClose={false} />
      <ApiProvider api={api} client={queryClient}>
        <MemoryRouter initialEntries={[initialRoute]}>
          <DrawerFixture />
        </MemoryRouter>
      </ApiProvider>
    </MantineProvider>
  </I18nProvider>
);
