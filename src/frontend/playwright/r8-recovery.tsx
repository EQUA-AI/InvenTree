/** Recovery-hook fixture only; no product screen or real records. */
import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { Alert, Button, Group, Stack, Text, Title } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api } from '../src/App';
import {
  useAllocationUseRecovery,
  useMaintenanceReceiptRecovery,
  usePurchaseApprovalRecovery,
  useRecordedDocumentRecovery
} from '../src/components/records/useProcurementRecovery';
import { messages } from '../src/locales/en/messages';
import { useUserState } from '../src/states/UserState';
import { FixtureTheme, prepareFixtureTheme } from './FixtureTheme';
import '@mantine/core/styles.css';

const params = new URLSearchParams(location.search);
prepareFixtureTheme(params);
i18n.load('en', messages);
i18n.activate('en');
api.defaults.timeout = 1500;
useUserState.setState({
  is_authed: true,
  authStatus: 'authenticated',
  user: { pk: 11, username: 'Fixture reviewer' } as any
});
const query = new QueryClient({
  defaultOptions: { queries: { retry: false } }
});

function Fixture() {
  const [work, setWork] = useState(3826);
  const [saved, setSaved] = useState('');
  const [batch, setBatch] = useState('R8-FIXTURE-A');
  const receipt = useMaintenanceReceiptRecovery({
    workOrderId: work,
    purchaseOrderId: work,
    onSaved: (stocks) =>
      setSaved(`Received stock ${stocks.map((row) => row.pk).join(', ')}`)
  });
  return (
    <Stack p='lg' maw={720}>
      <Title order={2}>Cedar Creek Pump 08</Title>
      <Text c='dimmed'>Recovery contract fixture · WO-{work}</Text>
      <Group>
        <Button
          disabled={receipt.pending || !!receipt.retained || receipt.blocked}
          onClick={() =>
            receipt.submit({
              maintenance_operation_key: `11:${crypto.randomUUID()}`,
              maintenance_requirement_version: 1,
              location: 7,
              items: [
                { line_item: 17, quantity: '1', status: 10, batch_code: batch }
              ]
            })
          }
        >
          Receive
        </Button>
        {receipt.retained && (
          <Button
            disabled={receipt.pending || receipt.blocked}
            onClick={() => receipt.retry()}
          >
            Recover receipt
          </Button>
        )}
      </Group>
      {receipt.notice && <Alert aria-live='polite'>{receipt.notice}</Alert>}
      {saved && <Text component='output'>{saved}</Text>}
      <Group>
        <Button variant='default' onClick={() => setBatch('R8-FIXTURE-EDITED')}>
          Edit fixture batch
        </Button>
        <Button
          variant='default'
          onClick={() => {
            setSaved('');
            setWork(work === 3826 ? 3827 : 3826);
          }}
        >
          Change scope
        </Button>
      </Group>
    </Stack>
  );
}
/** The same operation hooks used by native consumers, with isolated HTTP fixtures. */
function OtherOperations() {
  const [saved, setSaved] = useState('');
  const onSaved = (value: Record<string, unknown>) =>
    setSaved(String(value.state ?? value.status));
  const document = useRecordedDocumentRecovery({
    workOrderId: 3826,
    purchaseOrderId: 3826,
    onSaved
  });
  const use = useAllocationUseRecovery({
    workOrderId: 3826,
    allocationId: 13,
    onSaved
  });
  const purchase = usePurchaseApprovalRecovery({
    workOrderId: 3826,
    approvalId: 'fixture-approval',
    onSaved
  });
  const family = params.get('family');
  const operation =
    family === 'document' ? document : family === 'use' ? use : purchase;
  return (
    <Stack p='lg' maw={720}>
      <Title order={2}>Recovery contract fixture</Title>
      <Button
        disabled={
          operation.pending || operation.blocked || !!operation.retained
        }
        onClick={() => {
          if (family === 'document')
            void document.submit({
              operation_key: `fixture:${crypto.randomUUID()}`,
              to: 'supplier@example.com',
              from_address: 'buyer@example.com',
              buyer_name: 'Fixture buyer',
              ship_to: 'Fixture receiving street',
              provenance_kind: 'fixture'
            });
          else if (family === 'use') void use.submit({});
          else
            void purchase.submit({ revision: 1, review_hash: 'a'.repeat(64) });
        }}
      >
        Record operation
      </Button>
      {operation.retained && (
        <Button
          disabled={operation.pending || operation.blocked}
          onClick={() => operation.retry()}
        >
          Recover operation
        </Button>
      )}
      {operation.notice && <Alert aria-live='polite'>{operation.notice}</Alert>}
      <Text component='output'>{saved}</Text>
    </Stack>
  );
}
createRoot(document.getElementById('root')!).render(
  <I18nProvider i18n={i18n}>
    <FixtureTheme nativeTheme={false}>
      <QueryClientProvider client={query}>
        {params.has('family') ? <OtherOperations /> : <Fixture />}
      </QueryClientProvider>
    </FixtureTheme>
  </I18nProvider>
);
