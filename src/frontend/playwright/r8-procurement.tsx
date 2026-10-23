/** Shared disposable R8 fixtures for Work Parts, native PO, receiving and Closeout. */
import './aichat-drawer-lingui';
import '@mantine/core/styles.css';
import '@mantine/dates/styles.css';
import '@mantine/notifications/styles.css';
import 'mantine-datatable/styles.css';
import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { Button, Group, Stack, Text, Title } from '@mantine/core';
import { ModalsProvider } from '@mantine/modals';
import { Notifications } from '@mantine/notifications';
import { QueryClient, useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Link,
  MemoryRouter,
  Route,
  Routes,
  useLocation
} from 'react-router-dom';
import type { CloseoutPartUsageRow } from '../lib/types/Closeout';
import { api } from '../src/App';
import { ApiProvider } from '../src/contexts/ApiContext';
import { useReceiveLineItems } from '../src/forms/PurchaseOrderForms';
import PurchaseOrderDetail from '../src/pages/purchasing/PurchaseOrderDetail';
import { PreparedCloseout } from '../src/pages/tasks/workorders/PreparedCloseout';
import { WorkMaterialsPanel } from '../src/pages/tasks/workorders/WorkMaterialsPanel';
import { useGlobalStatusState } from '../src/states/GlobalStatusState';
import { useLocalState } from '../src/states/LocalState';
import { useGlobalSettingsState } from '../src/states/SettingsStates';
import { useUserState } from '../src/states/UserState';
import { FixtureTheme, prepareFixtureTheme } from './FixtureTheme';

const params = new URLSearchParams(window.location.search);
prepareFixtureTheme(params);
const surface = params.get('surface');
const receivingOrCloseout = surface === 'receive' || surface === 'closeout';
const receivingFixtureHost = 'r8-w2b-fixture';
if (receivingOrCloseout)
  useLocalState.setState({ hostKey: receivingFixtureHost });
const client = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } }
});
useGlobalStatusState.setState({
  status: {
    stockitem: {
      status_class: 'StockStatus',
      values: { OK: { key: 10, label: 'OK', name: 'OK', color: 'green' } }
    },
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
  user: receivingOrCloseout
    ? ({
        pk: 11,
        username: 'Fixture stores',
        is_superuser: true,
        roles: {},
        permissions: {}
      } as any)
    : ({
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
      } as any)
});
const items = [
  {
    pk: 91,
    quantity: 1,
    received: 0,
    part_detail: {
      pk: 71,
      name: 'Fixture seal/service kit',
      IPN: 'FIXTURE-SK',
      units: 'kit'
    },
    supplier_part_detail: { SKU: 'FIXTURE-SUPPLIER-SK' }
  }
];
function ReceiveFixture() {
  const [orderPk, setOrderPk] = useState(81);
  const modal = useReceiveLineItems({
    items: params.get('complete') ? [] : items,
    orderPk,
    destinationPk: 42
  });
  return (
    <>
      <Button onClick={modal.open}>Receive fixture line</Button>
      {params.has('scopeSwitch') && (
        <Button
          data-order={orderPk}
          onClick={() => setOrderPk(orderPk === 81 ? 82 : 81)}
        >
          Change receiving scope
        </Button>
      )}
      {modal.modal}
    </>
  );
}
function CloseoutFixture() {
  const query = useQuery({
    queryKey: ['fixture-materials'],
    queryFn: async () =>
      (
        await axios.get<CloseoutPartUsageRow[]>(
          '/api/fixture-closeout-materials/'
        )
      ).data
  });
  return (
    <PreparedCloseout
      workOrder={{
        id: 73,
        reference: 'FIXTURE-WO-73',
        title: 'Fixture seal replacement',
        lifecycle_status: 'verifying',
        lifecycle_version: 7
      }}
      updateStatus='ready'
      fields={{
        cause: 'Fixture seal wear',
        action: 'Fixture replacement recorded',
        result: 'Fixture verification retained',
        verification_summary: 'Fixture test result'
      }}
      captures={[]}
      readings={[]}
      partUsage={query.data}
      error={query.isError}
      changed={false}
      draftDowntime=''
      draftFollowUp=''
    />
  );
}

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
    <FixtureTheme nativeTheme={receivingOrCloseout}>
      <ApiProvider
        api={receivingOrCloseout ? axios.create() : api}
        client={client}
      >
        <ModalsProvider>
          <Notifications />
          <MemoryRouter
            initialEntries={[
              params.get('entry') ??
                (receivingOrCloseout
                  ? '/maintenance/work-orders/73?work_panel=closeout'
                  : '/maintenance/work-orders/826/parts/')
            ]}
          >
            {receivingOrCloseout ? (
              <Stack p='md'>
                <Text size='sm'>Representative fixture · R8 lane B</Text>
                {surface === 'receive' ? (
                  <ReceiveFixture />
                ) : (
                  <CloseoutFixture />
                )}
              </Stack>
            ) : (
              <Surface />
            )}
          </MemoryRouter>
        </ModalsProvider>
      </ApiProvider>
    </FixtureTheme>
  </I18nProvider>
);
