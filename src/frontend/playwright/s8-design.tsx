import './aichat-drawer-lingui';
import { i18n } from '@lingui/core';
import { Stack, Text } from '@mantine/core';
import '@mantine/core/styles.css';
import '@mantine/charts/styles.css';
import '@mantine/dates/styles.css';
import '@mantine/notifications/styles.css';
import 'mantine-datatable/styles.css';
import { QueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { Suspense, lazy } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import LayoutComponent from '../src/components/nav/Layout';
import { ApiProvider } from '../src/contexts/ApiContext';
import { useUserState } from '../src/states/UserState';
import { FixtureTheme, prepareFixtureTheme } from './FixtureTheme';

const Machines = lazy(() => import('../src/pages/assets/MachineIndex'));
const MachineDetail = lazy(() => import('../src/pages/assets/MachineDetail'));
const params = new URLSearchParams(window.location.search);
prepareFixtureTheme(params);
i18n.load('en', {});
i18n.activate('en');
useUserState.setState({
  is_authed: true,
  authStatus: 'authenticated',
  user: {
    pk: 11,
    username: 'S8 fixture reader',
    is_superuser: false,
    roles: {
      work_order:
        params.get('role') === 'editor'
          ? ['view', 'add', 'change']
          : params.get('role') === 'acknowledger'
            ? ['view', 'change']
            : params.get('role') === 'creator'
              ? ['view', 'add']
              : ['view']
    },
    permissions: { assetmachine: ['view'], workorder: ['view'] }
  } as any
});
const client = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } }
});
createRoot(document.getElementById('root')!).render(
  <FixtureTheme>
    <ApiProvider api={axios.create()} client={client}>
      <MemoryRouter
        initialEntries={[
          params.get('entry') ?? '/machines/index/sites/?location=12'
        ]}
      >
        <Stack gap={0}>
          <Text size='xs' c='dimmed'>
            S8 synthetic fixture · native panels · no live plant connection
          </Text>
          <Suspense fallback={<Text>Loading native panel…</Text>}>
            <Routes>
              <Route element={<LayoutComponent />}>
                <Route path='/machines/index/*' element={<Machines />} />
                <Route
                  path='/machines/machine/:id/*'
                  element={<MachineDetail />}
                />
              </Route>
            </Routes>
          </Suspense>
        </Stack>
      </MemoryRouter>
    </ApiProvider>
  </FixtureTheme>
);
