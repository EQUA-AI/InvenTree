/** Production-facing components with test-only API interception. No backend is provisioned. */
import './test-lingui';

import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { MantineProvider, Stack, Text } from '@mantine/core';
import '@mantine/core/styles.css';
import '@mantine/charts/styles.css';
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
import { MachineHealthPanel } from '../src/pages/assets/health/MachineHealthPanel';
import { MachineMetricsPanel } from '../src/pages/assets/locations/MachineMetricsPanel';
import { parseMachineScope } from '../src/pages/assets/locations/machineMetrics';
import MaintenanceBoard from '../src/pages/maintenance/MaintenanceBoard';
import WorkOrderDetail from '../src/pages/tasks/workorders/WorkOrderDetail';
import { useUserState } from '../src/states/UserState';

queryClient.setDefaultOptions({ queries: { retry: false } });

useUserState.setState({
  is_authed: true,
  authStatus: 'authenticated',
  user: { pk: 11, username: 'Metrics fixture', is_superuser: true } as any
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

function PanelFixture() {
  const [params, setParams] = useSearchParams();
  (window as any).__metricsTest.setScope = (location: number) =>
    setParams({ location: String(location) });
  const scope = parseMachineScope(params);
  return (
    <Stack p='md'>
      <MachineMetricsPanel
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
  mode === 'health'
    ? `/health/${entry}`
    : mode === 'board'
      ? `/maintenance/board/${entry}`
      : mode === 'machine'
        ? `/machines/machine/${machineId}/${entry}`
        : `/machines/index/sites/${entry}`
];

// Test hooks: cache inspection and the REAL sign-out clear path
// (UserState.clearUserState -> queryClient.clear()).
(window as any).__metricsTest = {
  signOut: () => useUserState.getState().setAuthenticated(false),
  queries: () =>
    queryClient
      .getQueryCache()
      .findAll()
      .map((q) => ({
        key: q.queryKey,
        status: q.state.status,
        error: String(q.state.error)
      })),
  cacheWithData: () =>
    queryClient
      .getQueryCache()
      .findAll()
      .filter(
        (query) =>
          Array.isArray(query.queryKey) &&
          query.queryKey[0] === 'machine-metrics' &&
          query.state.data !== undefined
      ).length,
  invalidateMetrics: () =>
    queryClient.invalidateQueries({ queryKey: ['machine-metrics'] })
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
                <Route
                  path='health/'
                  element={<MachineHealthPanel machineId={5} />}
                />
                <Route
                  path='maintenance/work-orders/:id/*'
                  element={<WorkOrderDetail />}
                />
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
