import './aichat-drawer-lingui';

import { i18n } from '@lingui/core';
import { t } from '@lingui/core/macro';
import { I18nProvider } from '@lingui/react';
import { Button, MantineProvider, Stack, Text } from '@mantine/core';
import '@mantine/core/styles.css';
import '@mantine/charts/styles.css';
import '@mantine/dates/styles.css';
import '@mantine/notifications/styles.css';
import '@mantine/spotlight/styles.css';
import 'mantine-contextmenu/styles.css';
import 'mantine-datatable/styles.css';
import { ModalsProvider } from '@mantine/modals';
import { Notifications } from '@mantine/notifications';
import { QueryClient, useIsFetching } from '@tanstack/react-query';
import axios from 'axios';
import { ContextMenuProvider } from 'mantine-contextmenu';
import { createRoot } from 'react-dom/client';
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate
} from 'react-router-dom';

import LayoutComponent from '../src/components/nav/Layout';
import { ReturnLink } from '../src/components/nav/ReturnLink';
import { ApiProvider } from '../src/contexts/ApiContext';
import EquipmentRegistry from '../src/pages/assets/EquipmentRegistry';
import MachineDetail from '../src/pages/assets/MachineDetail';
import MachineIndex from '../src/pages/assets/MachineIndex';
import PumpMimic from '../src/pages/assets/health/PumpMimic';
import PumphouseMimic from '../src/pages/assets/health/PumphouseMimic';
import { SignalTable } from '../src/pages/assets/health/SignalTable';
import { SignalTrendSparkline } from '../src/pages/assets/health/SignalTrend';
import { PerformancePanel } from '../src/pages/assets/performance/PerformancePanel';
import MaintenanceBoard from '../src/pages/maintenance/MaintenanceBoard';
import MaintenanceIndex from '../src/pages/maintenance/MaintenanceIndex';
import RepairPacketDetail from '../src/pages/repair/RepairPacketDetail';
import WorkOrderDetail from '../src/pages/tasks/workorders/WorkOrderDetail';
import { useLocalState } from '../src/states/LocalState';
import { useGlobalSettingsState } from '../src/states/SettingsStates';
import { useUserState } from '../src/states/UserState';
import { signalRows } from '../tests/performance/fixtures';

const params = new URLSearchParams(window.location.search);
const surface = params.get('surface') ?? 'signals';
const nativeReader =
  ['machine-explorer', 'maintenance-shell'].includes(surface) &&
  params.get('work_access') !== 'denied';
if (surface === 'maintenance-shell')
  useLocalState.setState({ hostKey: 's1-synthetic' });
const client = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } }
});

useGlobalSettingsState.setState({
  loaded: true,
  lookup: { CALENDAR_HORIZON_MONTHS: '12', WEEK_STARTS_ON: '1' }
});

useUserState.setState({
  is_authed: true,
  authStatus: 'authenticated',
  user: {
    pk: Number(params.get('actor') ?? 11),
    username: 'S0 synthetic fixture',
    is_superuser: !['viewer', 'editor'].includes(params.get('role') ?? ''),
    roles:
      params.get('role') === 'editor'
        ? { work_order: ['view', 'add', 'change', 'delete'] }
        : nativeReader
          ? {
              work_order: ['view'],
              ...(params.get('part_access') === 'reader'
                ? { part: ['view'] }
                : {})
            }
          : {},
    permissions:
      params.get('role') === 'editor'
        ? {
            assetmachine: ['view', 'add', 'change', 'delete'],
            workorder: ['view', 'add', 'change', 'delete']
          }
        : nativeReader
          ? { assetmachine: ['view'], workorder: ['view'] }
          : {}
  } as any
});

function QueryState() {
  const fetching = useIsFetching();
  return <Text data-testid='fixture-fetching'>{fetching}</Text>;
}

function NativeSurface() {
  const location = useLocation();
  const navigate = useNavigate();
  if (surface === 'performance') {
    return (
      <PerformancePanel
        machine={{ pk: 18, name: 'Synthetic pump', asset_type: 'pump' }}
      />
    );
  }
  if (surface === 'mimic') {
    const unit = params.get('pump');
    const station = Number(params.get('station') ?? 17);
    return unit ? (
      <PumpMimic stationId={station} unit={unit} />
    ) : (
      <PumphouseMimic stationId={station} />
    );
  }
  if (surface === 'standalone-trend') {
    return <SignalTrendSparkline machineId={18} bindingId={100} />;
  }
  if (surface === 'signals') {
    const signals = signalRows()
      .slice(0, 3)
      .map((signal) => ({
        ...signal,
        source_type: 'scada' as const,
        quality: 'good' as const,
        state: 'unknown' as const
      }));
    signals[0].value = 0;
    signals[1].value = null;
    return <SignalTable machineId={18} signals={signals} />;
  }
  if (
    ['machine-shell', 'machine-explorer', 'maintenance-shell'].includes(surface)
  ) {
    return (
      <>
        {surface !== 'machine-shell' && (
          <>
            <Text
              data-testid='destination'
              style={{ overflowWrap: 'anywhere' }}
            >
              {location.pathname + location.search}
            </Text>
            <Button onClick={() => navigate(-1)}>Fixture back</Button>
            <Button onClick={() => navigate(1)}>Fixture forward</Button>
          </>
        )}
        <ContextMenuProvider>
          <Routes>
            <Route element={<LayoutComponent />}>
              <Route
                path='/machines/machine/:id/*'
                element={<MachineDetail />}
              />
              <Route path='/machines/index/*' element={<MachineIndex />} />
              <Route path='/maintenance/*' element={<MaintenanceIndex />} />
              <Route
                path='/maintenance/work-orders/:id/*'
                element={<WorkOrderDetail />}
              />
              {/* R6 P13/P22: the labelled way back a native Part page offers. */}
              <Route path='/part/:id/*' element={<ReturnLink />} />
              <Route path='/stock/item/:id/*' element={<ReturnLink />} />
            </Route>
          </Routes>
        </ContextMenuProvider>
      </>
    );
  }
  return (
    <>
      <Text data-testid='destination'>
        {location.pathname + location.search}
      </Text>
      <Routes>
        <Route
          path='/maintenance/board'
          element={
            surface === 'maintenance' ? (
              <MaintenanceIndex />
            ) : (
              <MaintenanceBoard />
            )
          }
        />
        <Route path='/maintenance/*' element={<MaintenanceIndex />} />
        <Route
          path='/maintenance/work-orders/:id/*'
          element={<WorkOrderDetail />}
        />
        <Route path='/repair/packets/:id/*' element={<RepairPacketDetail />} />
        <Route path='/machines/registry/' element={<EquipmentRegistry />} />
        <Route path='/machines/machine/:id/*' element={<MachineDetail />} />
        {/* R6 P13: the labelled way back a native Part page offers. */}
        <Route path='/part/:id/*' element={<ReturnLink />} />
      </Routes>
    </>
  );
}

createRoot(document.getElementById('root')!).render(
  <I18nProvider i18n={i18n}>
    <MantineProvider
      forceColorScheme={params.get('scheme') === 'dark' ? 'dark' : 'light'}
    >
      <ApiProvider api={axios.create()} client={client}>
        <ModalsProvider>
          <Notifications />
          <MemoryRouter
            basename={params.get('basename') ?? '/'}
            initialEntries={[
              (params.get('basename') ?? '') +
                (params.get('entry') ?? '/maintenance/board')
            ]}
          >
            <Stack p='md'>
              <Text>
                {params.get('sprint') === 's1'
                  ? t`S1 synthetic fixture — native resources`
                  : t`S0 synthetic fixture — native baseline`}
              </Text>
              <Button
                variant='default'
                onClick={() => void client.invalidateQueries()}
              >
                {t`Refresh fixture`}
              </Button>
              <QueryState />
              <NativeSurface />
            </Stack>
          </MemoryRouter>
        </ModalsProvider>
      </ApiProvider>
    </MantineProvider>
  </I18nProvider>
);
