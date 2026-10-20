import './aichat-drawer-lingui';

import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { Button, Group, MantineProvider, Stack, Text } from '@mantine/core';
import '@mantine/core/styles.css';
import '@mantine/charts/styles.css';
import '@mantine/dates/styles.css';
import { QueryClient, useIsFetching } from '@tanstack/react-query';
import axios from 'axios';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';

import { ApiProvider } from '../src/contexts/ApiContext';
import { PerformancePanel } from '../src/pages/assets/performance/PerformancePanel';
import { useLocalState } from '../src/states/LocalState';
import { useUserState } from '../src/states/UserState';

/**
 * S3 fixture page for the native Performance tab: a pump or a station,
 * inside a router whose location the page can read and the tests can see.
 * The buttons are fixture controls standing in for the machine page's tab
 * navigation; they are not product chrome.
 */
const params = new URLSearchParams(window.location.search);
const pk = Number(params.get('machine') ?? 18);
const asset = params.get('asset') ?? 'pump';
const entry = params.get('entry') ?? `/machines/machine/${pk}/performance`;

useLocalState.setState({ hostKey: params.get('host') ?? 's3-synthetic' });
useUserState.setState({
  is_authed: true,
  authStatus: 'authenticated',
  user: {
    pk: Number(params.get('actor') ?? 11),
    username: 'S3 synthetic fixture',
    is_superuser: false,
    roles: { work_order: ['view'] },
    permissions: { assetmachine: ['view'], workorder: ['view'] }
  } as any
});

const client = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } }
});

function QueryState() {
  const fetching = useIsFetching();
  return <Text data-testid='fixture-fetching'>{fetching}</Text>;
}

function Harness() {
  const location = useLocation();
  const navigate = useNavigate();
  const onPerformance = location.pathname.endsWith('/performance');
  return (
    <Stack p='md' gap='sm'>
      <Text>S3 synthetic fixture — native Performance</Text>
      <Text
        data-testid='destination'
        size='xs'
        style={{ overflowWrap: 'anywhere' }}
      >
        {location.pathname + location.search}
      </Text>
      <Group gap='xs'>
        <Button
          size='xs'
          variant='default'
          onClick={() => navigate(`/machines/machine/${pk}/health`)}
        >
          Fixture leave Performance
        </Button>
        <Button size='xs' variant='default' onClick={() => navigate(-1)}>
          Fixture back
        </Button>
        <QueryState />
      </Group>
      {onPerformance && (
        <PerformancePanel
          machine={{
            pk,
            name: params.get('name') ?? 'Synthetic machine',
            asset_type: asset
          }}
        />
      )}
    </Stack>
  );
}

createRoot(document.getElementById('root')!).render(
  <I18nProvider i18n={i18n}>
    <MantineProvider
      forceColorScheme={params.get('scheme') === 'dark' ? 'dark' : 'light'}
    >
      <ApiProvider api={axios.create()} client={client}>
        <MemoryRouter
          basename={params.get('basename') ?? '/'}
          initialEntries={[(params.get('basename') ?? '') + entry]}
        >
          <Harness />
        </MemoryRouter>
      </ApiProvider>
    </MantineProvider>
  </I18nProvider>
);
