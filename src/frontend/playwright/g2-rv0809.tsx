/**
 * G2 RV08/RV09 private mounted baseline fixture (worker50).
 *
 * REAL (never mocked, never re-created): the actual Ask producer page
 * WorkOrderDetail (route id IS the WorkOrder PK; button data-testid
 * ask-about-work-order calls the real openGlobalAIChat), the real
 * AIChatDrawer, the real AIChatState/ThreadFocusState/UserState stores and
 * the drawer's real contextPreflight/commitFocusChange flow.
 *
 * SIMULATED (specs only, via Playwright route mocks, loopback only): auth
 * (seeded through the REAL setAuthenticated/setUser store transitions, no
 * login request) and HTTP. No production module export is mocked and no
 * RV15 fixture seam is reused or copied.
 */
import './aichat-drawer-lingui';

import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { MantineProvider } from '@mantine/core';
import '@mantine/core/styles.css';
import { Notifications } from '@mantine/notifications';
import '@mantine/notifications/styles.css';
import { useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { api, queryClient } from '../src/App';
import type { ConversationFocusRef } from '../src/components/aichat/conversationFocus';
import { AIChatDrawer } from '../src/components/nav/AIChatDrawer';
import { ApiProvider } from '../src/contexts/ApiContext';
import MachineDetail from '../src/pages/assets/MachineDetail';
import MaintenanceBoard from '../src/pages/maintenance/MaintenanceBoard';
import WorkOrderDetail from '../src/pages/tasks/workorders/WorkOrderDetail';
import {
  type AIChatTargetHint,
  useAIChatState
} from '../src/states/AIChatState';
import { useThreadFocusState } from '../src/states/ThreadFocusState';
import { useUserState } from '../src/states/UserState';

interface G2DrawerState {
  isOpen: boolean;
  targetHint: AIChatTargetHint | undefined;
  hintThreadId: string | null;
}

interface G2FocusState {
  version: number | null;
  focusLabel: string | null;
}

interface G2SelectResult {
  ok: boolean;
  code?: string;
}

/** Typed read/actuator seam: store READS plus REAL store ACTIONS only. */
export interface G2Harness {
  drawerState: () => G2DrawerState;
  focusState: () => G2FocusState;
  refreshFocus: () => Promise<void>;
  selectFocus: (ref: ConversationFocusRef | null) => Promise<G2SelectResult>;
  accountUserId: () => number | undefined;
}

declare global {
  interface Window {
    __g2?: G2Harness;
  }
}

// Wholly mocked auth through the REAL store transitions (no login request,
// no credentials; userId() derives from user.pk in the real store).
useUserState.getState().setAuthenticated(true);
useUserState.getState().setUser({
  pk: 11,
  username: 'g2 mounted fixture',
  first_name: '',
  last_name: '',
  email: '',
  is_staff: false,
  is_superuser: false,
  groups: [],
  profile: {
    language: 'en',
    theme: {},
    widgets: {},
    displayname: 'g2 mounted fixture',
    position: null,
    status: null,
    location: null,
    active: true,
    contact: null,
    type: 'user',
    organisation: null,
    primary_group: null
  }
});

function G2Fixture() {
  const opened = useAIChatState((state) => state.isOpen);
  useEffect(() => {
    document.body.dataset.g2Ready = '1';
  }, []);
  return (
    <AIChatDrawer
      opened={opened}
      onClose={() => useAIChatState.getState().close()}
    />
  );
}

window.__g2 = {
  drawerState: () => {
    const state = useAIChatState.getState();
    return {
      isOpen: state.isOpen,
      targetHint: state.targetHint,
      hintThreadId: state.hintThreadId
    };
  },
  focusState: () => {
    try {
      const state = useThreadFocusState.getState();
      return {
        version: state.version,
        focusLabel: state.focus ? state.focus.label : null
      };
    } catch {
      return { version: null, focusLabel: null };
    }
  },
  // Real native refresh (real HTTP GET through the spec's route mocks).
  refreshFocus: async () => {
    await useThreadFocusState.getState().refresh();
  },
  // Real native select (real focus PUT through the spec's route mocks).
  selectFocus: async (ref: ConversationFocusRef | null) => {
    return await useThreadFocusState.getState().select(ref);
  },
  accountUserId: () => useUserState.getState().userId()
};

const initialRoute =
  new URLSearchParams(window.location.search).get('route') ?? '/workorder/42/';

createRoot(document.getElementById('root')!).render(
  <I18nProvider i18n={i18n}>
    <MantineProvider defaultColorScheme='auto'>
      {/* Honest failure feedback renders through showNotification. */}
      <Notifications autoClose={false} />
      <ApiProvider api={api} client={queryClient}>
        <MemoryRouter initialEntries={[initialRoute]}>
          <Routes>
            <Route path='/machine/:id/*' element={<MachineDetail />} />
            <Route path='/maintenance/board/' element={<MaintenanceBoard />} />
            <Route path='/workorder/:id/' element={<WorkOrderDetail />} />
            <Route path='*' element={<WorkOrderDetail />} />
          </Routes>
          <G2Fixture />
        </MemoryRouter>
      </ApiProvider>
    </MantineProvider>
  </I18nProvider>
);
