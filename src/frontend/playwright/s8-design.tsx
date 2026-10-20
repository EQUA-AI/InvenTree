import { i18n } from '@lingui/core';
import { Stack, Text } from '@mantine/core';
import '@mantine/core/styles.css';
import '@mantine/charts/styles.css';
import '@mantine/dates/styles.css';
import '@mantine/notifications/styles.css';
import 'mantine-datatable/styles.css';
import { QueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { Suspense, lazy } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ApiProvider } from '../src/contexts/ApiContext';
import { useGlobalSettingsState } from '../src/states/SettingsStates';
import { useUserState } from '../src/states/UserState';
import { FixtureTheme, prepareFixtureTheme } from './FixtureTheme';
import { MachineFixtureRail } from './MachineFixtureRail';

const Machines = lazy(() => import('../src/pages/assets/MachineIndex'));
const Faults = lazy(() => import('../src/pages/assets/FaultHistoryPanel'));
const Equipment = lazy(() =>
  import('../src/pages/assets/equipment/EquipmentViewPanel').then((m) => ({
    default: m.EquipmentViewPanel
  }))
);
const Performance = lazy(() =>
  import('../src/pages/assets/performance/PerformancePanel').then((m) => ({
    default: m.PerformancePanel
  }))
);
const Pump = lazy(() => import('../src/pages/assets/health/PumpMimic'));
const params = new URLSearchParams(window.location.search);
prepareFixtureTheme(params);
i18n.load('en', {});
i18n.activate('en');
useGlobalSettingsState.setState({
  loaded: true,
  lookup: params.has('plant_timezone')
    ? { AIMMS_PLANT_TIMEZONE: params.get('plant_timezone')! }
    : {}
});
useUserState.setState({
  is_authed: true,
  authStatus: 'authenticated',
  user: {
    pk: 11,
    username: 'S8 fixture reader',
    is_superuser: false,
    roles: {
      work_order:
        params.get('role') === 'editor'
          ? ['view', 'add', 'change']
          : params.get('role') === 'acknowledger'
            ? ['view', 'change']
            : params.get('role') === 'creator'
              ? ['view', 'add']
              : ['view']
    },
    permissions: { assetmachine: ['view'], workorder: ['view'] }
  } as any
});
const client = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } }
});
createRoot(document.getElementById('root')!).render(
  <FixtureTheme>
    <ApiProvider api={axios.create()} client={client}>
      <MemoryRouter
        initialEntries={[
          params.get('entry') ?? '/machines/index/sites/?location=12'
        ]}
      >
        <Stack p='md'>
          <Text size='xs' c='dimmed'>
            S8 synthetic fixture · native panels · no live plant connection
          </Text>
          <Suspense fallback={<Text>Loading native panel…</Text>}>
            <Routes>
              <Route path='/machines/index/*' element={<Machines />} />
              <Route
                path='/machines/machine/:id/*'
                element={
                  <MachineFixtureRail
                    faults={<Faults machineId={18} />}
                    equipment={
                      <Equipment
                        machine={{ pk: 18, name: 'Synthetic pump' }}
                        schematic={<Pump stationId={17} unit='P1' />}
                      />
                    }
                    performance={
                      <Performance
                        machine={{
                          pk: 18,
                          name: 'Synthetic pump',
                          asset_type: 'pump'
                        }}
                      />
                    }
                  />
                }
              />
            </Routes>
          </Suspense>
        </Stack>
      </MemoryRouter>
    </ApiProvider>
  </FixtureTheme>
);
