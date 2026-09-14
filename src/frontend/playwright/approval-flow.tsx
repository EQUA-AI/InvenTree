/** Recording-only fixture; no credentials, providers or real business records. */
import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { Button, MantineProvider, Stack, Tabs } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api } from '../src/App';
import { ApprovalInboxPanel } from '../src/components/ai/ApprovalInboxPanel';
import { VoiceDecisionCard } from '../src/components/ai/VoiceDecisionCard';
import { messages } from '../src/locales/en/messages';
import { useVoiceDecisionState } from '../src/states/VoiceDecisionState';
import '@mantine/core/styles.css';

i18n.load('en', messages);
i18n.activate('en');
api.defaults.timeout = 5000;
const client = new QueryClient({
  defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
});
function Fixture() {
  const [tab, setTab] = useState<string | null>('Chat');
  return (
    <Stack maw={760} p='md'>
      <Button
        onClick={async () => {
          useVoiceDecisionState.getState().setSession('recording-session');
          useVoiceDecisionState
            .getState()
            .applyTurn(
              'recording-session',
              (await api.get('/api/recording/decision')).data
            );
        }}
      >
        Load shared review
      </Button>
      <VoiceDecisionCard />
      <Tabs value={tab} onChange={setTab}>
        <Tabs.List>
          {['Chat', 'Approvals', 'History'].map((name) => (
            <Tabs.Tab key={name} value={name}>
              {name}
            </Tabs.Tab>
          ))}
        </Tabs.List>
      </Tabs>
      <ApprovalInboxPanel
        key={tab}
        statuses={['pending', 'in_review']}
        emptyText='No requests'
      />
    </Stack>
  );
}
createRoot(document.getElementById('root')!).render(
  <I18nProvider i18n={i18n}>
    <MantineProvider>
      <QueryClientProvider client={client}>
        <Fixture />
      </QueryClientProvider>
    </MantineProvider>
  </I18nProvider>
);
