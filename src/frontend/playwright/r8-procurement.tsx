/** Explicit disposable fixtures; never reads or writes the showcase database. */
import './aichat-drawer-lingui';
import '@mantine/core/styles.css';
import '@mantine/notifications/styles.css';
import '@mantine/dates/styles.css';
import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { Button, Stack, Text } from '@mantine/core';
import { ModalsProvider } from '@mantine/modals';
import { Notifications } from '@mantine/notifications';
import { QueryClient, useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import type { CloseoutPartUsageRow } from '../lib/types/Closeout';
import { ApiProvider } from '../src/contexts/ApiContext';
import { useReceiveLineItems } from '../src/forms/PurchaseOrderForms';
import { PreparedCloseout } from '../src/pages/tasks/workorders/PreparedCloseout';
import { useGlobalStatusState } from '../src/states/GlobalStatusState';
import { useLocalState } from '../src/states/LocalState';
import { useGlobalSettingsState } from '../src/states/SettingsStates';
import { useUserState } from '../src/states/UserState';
import { FixtureTheme, prepareFixtureTheme } from './FixtureTheme';
const params = new URLSearchParams(location.search);
prepareFixtureTheme(params);
useLocalState.setState({ hostKey: 'r8-w2b-fixture' });
useUserState.setState({
  is_authed: true,
  authStatus: 'authenticated',
  user: {
    pk: 11,
    username: 'Fixture stores',
    is_superuser: true,
    roles: {},
    permissions: {}
  } as any
});
useGlobalSettingsState.setState({ loaded: true, lookup: {} });
useGlobalStatusState.setState({
  status: {
    stockitem: {
      status_class: 'StockStatus',
      values: { OK: { key: 10, label: 'OK', name: 'OK', color: 'green' } }
    }
  }
});
const client = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } }
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
  const modal = useReceiveLineItems({
    items: params.get('complete') ? [] : items,
    orderPk: 81,
    destinationPk: 42
  });
  return (
    <>
      <Button onClick={modal.open}>Receive fixture line</Button>
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
createRoot(document.getElementById('root')!).render(
  <I18nProvider i18n={i18n}>
    <FixtureTheme nativeTheme>
      <ApiProvider api={axios.create()} client={client}>
        <ModalsProvider>
          <Notifications />
          <MemoryRouter
            initialEntries={['/maintenance/work-orders/73?work_panel=closeout']}
          >
            <Stack p='md'>
              <Text size='sm'>Representative fixture · R8 lane B</Text>
              {params.get('surface') === 'receive' ? (
                <ReceiveFixture />
              ) : (
                <CloseoutFixture />
              )}
            </Stack>
          </MemoryRouter>
        </ModalsProvider>
      </ApiProvider>
    </FixtureTheme>
  </I18nProvider>
);
