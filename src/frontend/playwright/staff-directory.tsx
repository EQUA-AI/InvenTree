import './test-lingui';
import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { Button, MantineProvider, Stack, Text } from '@mantine/core';
import '@mantine/core/styles.css';
import '@mantine/notifications/styles.css';
import { Notifications } from '@mantine/notifications';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { queryClient } from '../src/App';
import { OccupationalProfileEditor } from '../src/components/staff/OccupationalProfileEditor';
import { StaffAssignmentModal } from '../src/components/staff/StaffAssignmentModal';
import { StaffSummaryPopover } from '../src/components/staff/StaffSummaryPopover';
import { ApiProvider } from '../src/contexts/ApiContext';
import { WorkOrderCreateModal } from '../src/pages/maintenance/components/WorkOrderCreateModal';
import { useUserState } from '../src/states/UserState';

const params = new URLSearchParams(window.location.search);
const mode = params.get('mode') ?? 'assignment';
const permissions = {
  staff_directory: ['view'],
  workorder: params.get('no-plan') ? ['assign'] : ['assign', 'plan'],
  ...(params.get('override') === '1'
    ? { assignment_trade_mismatch: ['override'] }
    : {}),
  user: ['change']
};
useUserState.setState({
  is_authed: true,
  authStatus: 'authenticated',
  user: {
    pk: 3,
    username: 'Supervisor',
    is_staff: true,
    permissions: params.get('denied') ? {} : permissions
  } as any
});

function Fixture() {
  const [opened, setOpened] = useState(true);
  const [receipt, setReceipt] = useState(false);
  const workOrder = useQuery({
    queryKey: ['work-order', 40],
    enabled: mode === 'assignment',
    queryFn: async () => (await axios.get('/api/kanban/work-orders/40/')).data
  });
  if (mode === 'assignment' && !workOrder.data)
    return <Text>Loading work order</Text>;
  return (
    <Stack p='md' mih='100vh'>
      <Button
        onClick={() => {
          useUserState.getState().setUser({
            ...useUserState.getState().user!,
            pk: 4,
            permissions: {}
          });
        }}
      >
        Revoke permissions
      </Button>
      {receipt && <Text>Saved successfully</Text>}
      {!opened && mode === 'assignment' && (
        <Button onClick={() => setOpened(true)}>Reopen assignment</Button>
      )}
      {mode === 'profile' ? (
        <OccupationalProfileEditor
          userId={12}
          onSaved={() => setReceipt(true)}
        />
      ) : mode === 'summary' ? (
        <>
          <StaffSummaryPopover
            userId={12}
            label='Alex Lee'
            scope={{ workOrderId: 40 }}
          />
          <StaffSummaryPopover
            userId={null}
            label='Legacy contractor'
            scope={{ workOrderId: 40 }}
          />
        </>
      ) : mode === 'create' ? (
        <WorkOrderCreateModal
          opened={opened}
          machineId={5}
          onClose={() => setOpened(false)}
          onCreated={() => setReceipt(true)}
        />
      ) : (
        opened && (
          <StaffAssignmentModal
            workOrder={workOrder.data}
            {...(params.get('card') ? { cardId: 81, cardAssignedTo: 12 } : {})}
            onClose={() => setOpened(false)}
            onSaved={() => setReceipt(true)}
          />
        )
      )}
    </Stack>
  );
}

createRoot(document.getElementById('root')!).render(
  <I18nProvider i18n={i18n}>
    <MantineProvider>
      <Notifications />
      <ApiProvider api={axios.create()} client={queryClient}>
        <MemoryRouter>
          <Fixture />
        </MemoryRouter>
      </ApiProvider>
    </MantineProvider>
  </I18nProvider>
);
