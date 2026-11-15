/** Synthetic component qualification using the production assistant and SDK. */
import '@mantine/core/styles.css';
import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import {
  Button,
  Container,
  Group,
  MantineProvider,
  Stack,
  Text,
  Title
} from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { AssistantMessageRenderer } from '../../src/components/aichat/AssistantMessageRenderer';
import { normalizeSourceObservation } from '../../src/components/aichat/sourceObservation';
import {
  createInvenTreeTheme,
  cssVariablesResolver
} from '../../src/contexts/ThemeContext';
import { runAguiTurn } from '../../src/hooks/AguiTransport';
import type { ChatMessage } from '../../src/hooks/UseAIChat';
import { useLocalState } from '../../src/states/LocalState';
import { body, payload } from './fixture';

i18n.load('en', {});
i18n.activate('en');
const initial: ChatMessage = {
  id: 'source-fixture',
  role: 'assistant',
  content: body,
  timestamp: new Date('2026-11-13T12:00:00Z'),
  sourceObservation: normalizeSourceObservation(payload) ?? undefined
};

function App() {
  const [message, setMessage] = useState(initial);
  const [status, setStatus] = useState('Synthetic reload fixture');
  const project = (value: unknown) =>
    setMessage((m) => ({
      ...m,
      sourceObservation: normalizeSourceObservation(value) ?? undefined
    }));
  const restore = () => {
    setMessage(initial);
    setStatus('Synthetic reload fixture');
  };
  const run = async () => {
    setMessage({ ...initial, content: '', sourceObservation: undefined });
    setStatus('Receiving synthetic AG-UI stream');
    await runAguiTurn({
      url: '/synthetic-stream',
      threadId: 'fixture-thread',
      message: 'Read source',
      idempotencyKey: 'fixture-1',
      signal: new AbortController().signal,
      callbacks: {
        onTextStart: () => {},
        onTextDelta: (_messageId, delta) =>
          setMessage((m) => ({ ...m, content: m.content + delta })),
        onMessagesSnapshot: (content) => setMessage((m) => ({ ...m, content })),
        onToolStart: () => {},
        onToolEnd: () => {},
        onToolStatus: () => {},
        onQuestion: () => {},
        onEntities: () => {},
        onMediaEvidence: () => {},
        onProvenance: () => {},
        onEvidenceAnalysis: () => {},
        onAnalysisProgress: () => {},
        onProposalsRefresh: () => {},
        onSourceObservation: project
      }
    });
    setStatus('Synthetic AG-UI stream complete');
  };
  return (
    <Container size='sm' py='lg'>
      <Stack gap='md'>
        <div>
          <Text size='xs' c='dimmed'>
            LOCAL COMPONENT QUALIFICATION · SYNTHETIC DATA
          </Text>
          <Title order={1} size='h3'>
            Assistant source observation
          </Title>
        </div>
        <Group gap='xs'>
          <Button size='xs' variant='default' onClick={restore}>
            Reload fixture
          </Button>
          <Button size='xs' variant='default' onClick={() => void run()}>
            Stream fixture
          </Button>
          <Button
            size='xs'
            variant='default'
            onClick={() => {
              setMessage({
                ...initial,
                content:
                  'Source-backed answer unavailable under current access or source state.',
                sourceObservation: {
                  schema_version: 'aimms.source-observation.v1',
                  state: 'unavailable'
                }
              });
              setStatus('Synthetic denied fixture');
            }}
          >
            Access unavailable
          </Button>
          <Button
            size='xs'
            variant='default'
            onClick={() => {
              setMessage({
                ...initial,
                content: 'The source read could not be completed.',
                sourceObservation: undefined
              });
              setStatus('Synthetic error fixture');
            }}
          >
            Read error
          </Button>
          <Button
            size='xs'
            variant='default'
            onClick={() => {
              project({
                ...payload,
                returned_count: 0,
                gaps: [],
                window_complete: true,
                citations: []
              });
              setMessage((m) => ({
                ...m,
                content: 'No samples were returned for this selected window.'
              }));
              setStatus('Synthetic empty fixture');
            }}
          >
            Empty window
          </Button>
        </Group>
        <Text component='output' size='xs'>
          {status}
        </Text>
        <AssistantMessageRenderer
          message={message}
          threadId='fixture-thread'
          aiHost=''
        />
      </Stack>
    </Container>
  );
}

const client = new QueryClient();
createRoot(document.getElementById('root')!).render(
  <I18nProvider i18n={i18n}>
    <QueryClientProvider client={client}>
      <MantineProvider
        theme={createInvenTreeTheme(useLocalState.getState().userTheme)}
        cssVariablesResolver={cssVariablesResolver}
      >
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </MantineProvider>
    </QueryClientProvider>
  </I18nProvider>
);
