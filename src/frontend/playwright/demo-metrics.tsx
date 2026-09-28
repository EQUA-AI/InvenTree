/**
 * Demo-metrics browser fixture.
 *
 * Real components (DemoMetricsPanel, MachineDetail, MaintenanceBoard) under a
 * real MemoryRouter; every API response is supplied by Playwright route mocks.
 * This is MOCKED BROWSER RENDERING — not a real backend E2E run. A real E2E
 * needs a backend with an applied demo-metrics session and is tracked as a
 * parent-side item.
 */
import './demo-metrics-lingui';

import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { MantineProvider, Stack, Text } from '@mantine/core';
import '@mantine/core/styles.css';
import axios from 'axios';
import type { ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useSearchParams
} from 'react-router-dom';

import { queryClient } from '../src/App';
import { ApiProvider } from '../src/contexts/ApiContext';
import MachineDetail from '../src/pages/assets/MachineDetail';
import { DemoMetricsPanel } from '../src/pages/assets/locations/DemoMetricsPanel';
import { parseDemoScope } from '../src/pages/assets/locations/demoMetrics';
import MaintenanceBoard from '../src/pages/maintenance/MaintenanceBoard';
import { useUserState } from '../src/states/UserState';

useUserState.setState({
  is_authed: true,
  authStatus: 'authenticated',
  user: { pk: 11, username: 'Demo fixture', is_superuser: true } as any
});

function Destination() {
  const location = useLocation();
  return (
    <Text data-testid='destination' size='sm' c='dimmed'>
      {location.pathname + location.search}
    </Text>
  );
}

/**
 * Mirrors the real app: authenticated views unmount on sign-out, so cleared
 * queries are not immediately recreated by still-mounted observers.
 */
function Shell({ children }: { children: ReactNode }) {
  const authed = useUserState((s) => s.is_authed);
  if (!authed) return <Text>Signed out</Text>;
  return children;
}

/** Mirrors LocationWorkspace: the demo session rides the URL. */
function PanelFixture() {
  const [params, setParams] = useSearchParams();
  const scope = parseDemoScope(params);
  return (
    <Stack p='md'>
      <DemoMetricsPanel
        session={scope.session}
        onSessionChange={(value) => {
          const next = new URLSearchParams(params);
          if (value) next.set('demo_session', value);
          else next.delete('demo_session');
          setParams(next);
        }}
        locationId={scope.location ?? undefined}
        descendants={!scope.direct}
      />
    </Stack>
  );
}

const params = new URLSearchParams(window.location.search);
const mode = params.get('mode') ?? 'panel';
const machineId = params.get('machine') ?? '5';
const passthrough = new URLSearchParams(params);
for (const drop of ['mode', 'machine', 'hist', 'fail']) {
  passthrough.delete(drop);
}
const search = passthrough.toString();
const entry = search ? `?${search}` : '';
const initialEntries = [
  mode === 'board'
    ? `/maintenance/board/${entry}`
    : mode === 'machine'
      ? `/machines/machine/${machineId}/${entry}`
      : `/machines/index/sites/${entry}`
];

// Test hooks: cache inspection and the REAL sign-out clear path
// (UserState.clearUserState -> queryClient.clear()).
(window as any).__demoTest = {
  signOut: () => useUserState.getState().setAuthenticated(false),
  demoCacheWithData: () =>
    queryClient
      .getQueryCache()
      .findAll()
      .filter(
        (query) =>
          Array.isArray(query.queryKey) &&
          query.queryKey[0] === 'demo-metrics' &&
          query.state.data !== undefined
      ).length,
  invalidateDemo: () =>
    queryClient.invalidateQueries({ queryKey: ['demo-metrics'] })
};

createRoot(document.getElementById('root')!).render(
  <I18nProvider i18n={i18n}>
    <MantineProvider>
      <ApiProvider api={axios.create()} client={queryClient}>
        <MemoryRouter initialEntries={initialEntries}>
          <Shell>
            <Stack p='md'>
              <Destination />
              <Routes>
                <Route path='machines/index/*' element={<PanelFixture />} />
                <Route
                  path='machines/machine/:id/*'
                  element={<MachineDetail />}
                />
                <Route
                  path='maintenance/board/*'
                  element={<MaintenanceBoard />}
                />
              </Routes>
            </Stack>
          </Shell>
        </MemoryRouter>
      </ApiProvider>
    </MantineProvider>
  </I18nProvider>
);
