import './aichat-drawer-lingui';
import '@mantine/core/styles.css';
import '@mantine/dates/styles.css';
import '@mantine/notifications/styles.css';
import 'mantine-datatable/styles.css';
import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { Button, Group, Stack, Text, Title } from '@mantine/core';
import { ModalsProvider } from '@mantine/modals';
import { QueryClient } from '@tanstack/react-query';
import { createRoot } from 'react-dom/client';
import {
  Link,
  MemoryRouter,
  Route,
  Routes,
  useLocation
} from 'react-router-dom';
import { api } from '../src/App';
import { ApiProvider } from '../src/contexts/ApiContext';
import PurchaseOrderDetail from '../src/pages/purchasing/PurchaseOrderDetail';
import { WorkMaterialsPanel } from '../src/pages/tasks/workorders/WorkMaterialsPanel';
import { useGlobalStatusState } from '../src/states/GlobalStatusState';
import { useGlobalSettingsState } from '../src/states/SettingsStates';
import { useUserState } from '../src/states/UserState';
import { FixtureTheme, prepareFixtureTheme } from './FixtureTheme';

const params = new URLSearchParams(window.location.search);
prepareFixtureTheme(params);
const client = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } }
});
useGlobalStatusState.setState({
  status: {
    purchaseorder: {
      status_class: 'PurchaseOrderStatus',
      values: {
        PENDING: {
          key: 10,
          label: 'Pending',
          name: 'PENDING',
          color: 'default'
        },
        PLACED: { key: 20, label: 'Placed', name: 'PLACED', color: 'primary' },
        ON_HOLD: {
          key: 25,
          label: 'On hold',
          name: 'ON_HOLD',
          color: 'warning'
        },
        COMPLETE: {
          key: 30,
          label: 'Complete',
          name: 'COMPLETE',
          color: 'success'
        },
        CANCELLED: {
          key: 40,
          label: 'Cancelled',
          name: 'CANCELLED',
          color: 'danger'
        }
      }
    }
  }
});
const readOnly = params.get('role') === 'reader';
useGlobalSettingsState.setState({
  loaded: true,
  lookup: { INVENTREE_DEFAULT_CURRENCY: 'USD' }
});
useUserState.setState({
  is_authed: true,
  authStatus: 'authenticated',
  user: {
    pk: Number(params.get('actor') ?? 11),
    username: 'r8-fixture-lane-a',
    first_name: 'Fixture',
    last_name: 'Buyer',
    email: 'buyer@example.com',
    is_superuser: false,
    roles: {
      work_order: ['view'],
      part: ['view'],
      stock: ['view'],
      purchase_order: readOnly ? ['view'] : ['view', 'change', 'add']
    },
    permissions: readOnly
      ? {}
      : {
          jobkit: ['manage'],
          email: ['view'],
          approval: ['review'],
          purchaseorder: ['view', 'change', 'add']
        },
    groups: null,
    profile: { language: 'en' }
  } as any
});
function Surface() {
  const location = useLocation();
  return (
    <Stack p={{ base: 'sm', sm: 'lg' }}>
      <Group justify='space-between'>
        <Text size='xs'>R8 lane A · Representative fixture</Text>
        <Button
          size='xs'
          variant='subtle'
          onClick={() => void client.invalidateQueries()}
        >
          Refresh fixture
        </Button>
      </Group>
      <Text data-testid='destination' size='xs'>
        {location.pathname + location.search}
      </Text>
      <Routes>
        <Route
          path='/maintenance/work-orders/:id/*'
          element={
            <Stack>
              <Title order={2}>Fixture Pump 08 · Parts</Title>
              <WorkMaterialsPanel
                workOrderId={826}
                label='WO-FIXTURE-3826'
                fallback={<Text>Legacy required parts fixture</Text>}
              />
            </Stack>
          }
        />
        <Route
          path='/purchasing/purchase-order/:id/*'
          element={<PurchaseOrderDetail />}
        />
        <Route
          path='*'
          element={
            <Link to='/maintenance/work-orders/826/parts/'>
              Return to Work fixture
            </Link>
          }
        />
      </Routes>
    </Stack>
  );
}
createRoot(document.getElementById('root')!).render(
  <I18nProvider i18n={i18n}>
    <FixtureTheme nativeTheme={false}>
      <ApiProvider api={api} client={client}>
        <ModalsProvider>
          <MemoryRouter
            initialEntries={[
              params.get('entry') ?? '/maintenance/work-orders/826/parts/'
            ]}
          >
            <Surface />
          </MemoryRouter>
        </ModalsProvider>
      </ApiProvider>
    </FixtureTheme>
  </I18nProvider>
);
