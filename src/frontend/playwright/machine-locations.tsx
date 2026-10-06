/**
 * Machine location workspace / machine detail browser fixture (packages C, D
 * and machine E of the approved plan).
 *
 * Real components (LocationWorkspace, MachineDetail) under a real
 * MemoryRouter; every API response is supplied by Playwright route mocks from
 * tests/pages/pui_asset_locations.spec.ts. This is MOCKED BROWSER RENDERING —
 * not a real backend E2E run. Unique machine-* filenames keep this harness
 * independent of the chat worker's fixtures.
 * The provider follows the emulated system theme for visual QA.
 *
 * The Lingui bootstrap is shared with the chat fixture so
 * module-level translation calls stay safe during static imports.
 */
import './aichat-drawer-lingui';

import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { MantineProvider, Text } from '@mantine/core';
import '@mantine/core/styles.css';
import axios from 'axios';
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
import { LocationWorkspace } from '../src/pages/assets/locations/LocationWorkspace';
import { useUserState } from '../src/states/UserState';

useUserState.setState({
  is_authed: true,
  authStatus: 'authenticated',
  user: { pk: 11, username: 'Machine fixture', is_superuser: true } as any
});

/** Mirrors the router state on screen so tests can assert navigation targets. */
function Destination() {
  const location = useLocation();
  return (
    <Text data-testid='destination' size='sm' c='dimmed'>
      {location.pathname + location.search}
    </Text>
  );
}

/** Test hook: mutate the router query string (cohort changes, back/forward). */
function TestHooks() {
  const [params, setParams] = useSearchParams();
  (window as any).__machineTest = {
    setSearch: (key: string, value: string | null) => {
      const next = new URLSearchParams(params);
      if (value) next.set(key, value);
      else next.delete(key);
      setParams(next);
    }
  };
  return null;
}

const params = new URLSearchParams(window.location.search);
const mode = params.get('mode') ?? 'workspace';
const viewParam = params.get('view') ?? 'sites';
const view =
  viewParam === 'all'
    ? 'all'
    : viewParam === 'unassigned'
      ? 'unassigned'
      : 'sites';
const machineId = params.get('machine') ?? '5';
const passthrough = new URLSearchParams(params);
for (const drop of ['mode', 'view', 'machine']) {
  passthrough.delete(drop);
}
const search = passthrough.toString();
const entry = search ? `?${search}` : '';
const panel =
  view === 'sites' ? 'sites' : view === 'all' ? 'machines' : 'unassigned';
const initialEntries = [
  mode === 'machine'
    ? `/machines/machine/${machineId}/${entry}`
    : `/machines/index/${panel}/${entry}`
];

createRoot(document.getElementById('root')!).render(
  <I18nProvider i18n={i18n}>
    <MantineProvider defaultColorScheme='auto'>
      <ApiProvider api={axios.create()} client={queryClient}>
        <MemoryRouter initialEntries={initialEntries}>
          <TestHooks />
          <Routes>
            <Route
              path='/machines/machine/:id/*'
              element={
                <>
                  <Destination />
                  <MachineDetail />
                </>
              }
            />
            <Route
              path='/machines/index/:panel/*'
              element={
                <>
                  <Destination />
                  <LocationWorkspace view={view} />
                </>
              }
            />
          </Routes>
        </MemoryRouter>
      </ApiProvider>
    </MantineProvider>
  </I18nProvider>
);
