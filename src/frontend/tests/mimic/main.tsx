import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { MantineProvider } from '@mantine/core';
import '@mantine/core/styles.css';
import { QueryClient } from '@tanstack/react-query';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { ApiProvider } from '../../src/contexts/ApiContext';
import PumpMimic from '../../src/pages/assets/health/PumpMimic';
import PumphouseMimic from '../../src/pages/assets/health/PumphouseMimic';
import { useUserState } from '../../src/states/UserState';
import { api } from './api';

i18n.load('en', {});
i18n.activate('en');
const client = new QueryClient({
  defaultOptions: { queries: { retry: false } }
});
const params = new URLSearchParams(window.location.search);
// This isolated harness has no login. Supply the native roles explicitly so
// alarm-action fixtures exercise the real guards as an authorized operator.
const viewer = params.get('role') === 'viewer';
useUserState.setState({
  is_authed: true,
  authStatus: 'authenticated',
  user: {
    pk: 11,
    username: 'Synthetic mimic operator',
    first_name: 'Synthetic',
    last_name: 'Operator',
    email: '',
    is_superuser: false,
    groups: [],
    roles: { work_order: viewer ? ['view'] : ['view', 'add', 'change'] },
    permissions: {
      assetmachine: ['view'],
      workorder: viewer ? ['view'] : ['view', 'add', 'change']
    },
    profile: {
      language: 'en',
      theme: null,
      widgets: null,
      displayname: null,
      position: null,
      status: null,
      location: null,
      active: true,
      contact: null,
      type: '',
      organisation: null,
      primary_group: null
    }
  }
});
const root = document.getElementById('root');
if (root)
  createRoot(root).render(
    <I18nProvider i18n={i18n}>
      <ApiProvider api={api} client={client}>
        <MantineProvider
          forceColorScheme={params.get('scheme') === 'dark' ? 'dark' : 'light'}
        >
          <MemoryRouter>
            {params.get('pump') ? (
              <PumpMimic
                stationId={Number(params.get('station') ?? 17)}
                unit={params.get('pump') as string}
              />
            ) : (
              <PumphouseMimic stationId={Number(params.get('station') ?? 17)} />
            )}
          </MemoryRouter>
        </MantineProvider>
      </ApiProvider>
    </I18nProvider>
  );
