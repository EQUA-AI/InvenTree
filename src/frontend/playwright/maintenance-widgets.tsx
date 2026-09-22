/** Dashboard presentation fixture. API responses are supplied by Playwright. */
import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import {
  Button,
  MantineProvider,
  Paper,
  SimpleGrid,
  Stack,
  Text
} from '@mantine/core';
import { QueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { ChatActionProposalList } from '../src/components/ai/ChatActionProposals';
import MaintenanceWidget from '../src/components/dashboard/widgets/MaintenanceWidget';
import { maintenanceMetrics } from '../src/components/dashboard/widgets/maintenanceMetrics';
import { ApiProvider } from '../src/contexts/ApiContext';
import { useRiskScope } from '../src/hooks/UseRiskScope';
import { useChatProposals } from '../src/hooks/useChatProposals';
import { messages } from '../src/locales/en/messages';
import { useUserState } from '../src/states/UserState';
import '@mantine/core/styles.css';

i18n.load('en', messages);
i18n.activate('en');
useUserState.setState({
  is_authed: true,
  user: { pk: 11, username: 'Widget fixture', is_superuser: true } as any
});
const client = new QueryClient();
function ProposalFixture() {
  const [opened, setOpened] = useState(false);
  const proposals = useChatProposals(opened);
  return (
    <Stack>
      <Button onClick={() => setOpened(!opened)}>
        {opened ? 'Close proposals' : 'Open proposals'}
      </Button>
      {opened && <ChatActionProposalList {...proposals} />}
    </Stack>
  );
}
function RiskFixture() {
  const scope = useRiskScope();
  return (
    <Text>
      {scope.isLoading
        ? 'Loading risk'
        : scope.unavailable
          ? 'Risk unavailable'
          : 'Risk ready'}
    </Text>
  );
}
function Fixture() {
  const location = useLocation();
  const metric = new URLSearchParams(window.location.search).get('metric');
  const mode = new URLSearchParams(window.location.search).get('mode');
  if (mode === 'proposals') return <ProposalFixture />;
  if (mode === 'risk') return <RiskFixture />;
  return (
    <Stack p='md'>
      <Text data-testid='destination'>{location.pathname}</Text>
      <Button
        onClick={() =>
          void client.invalidateQueries({ queryKey: ['maintenance-metrics'] })
        }
      >
        Refresh fixture
      </Button>
      <Button
        onClick={() =>
          useUserState.setState({
            user: { pk: 11, is_superuser: false, roles: {} } as any
          })
        }
      >
        Revoke role
      </Button>
      <SimpleGrid cols={{ base: 1, md: 3 }}>
        {maintenanceMetrics()
          .filter((m) => !metric || m.id === metric)
          .map((definition) => (
            <Paper p='sm' withBorder key={definition.id}>
              <MaintenanceWidget definition={definition} />
            </Paper>
          ))}
      </SimpleGrid>
    </Stack>
  );
}
createRoot(document.getElementById('root')!).render(
  <I18nProvider i18n={i18n}>
    <MantineProvider>
      <ApiProvider api={axios.create()} client={client}>
        <MemoryRouter>
          <Fixture />
        </MemoryRouter>
      </ApiProvider>
    </MantineProvider>
  </I18nProvider>
);
