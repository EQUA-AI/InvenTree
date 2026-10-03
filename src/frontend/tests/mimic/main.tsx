import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { MantineProvider } from '@mantine/core';
import '@mantine/core/styles.css';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import PumpMimic from '../../src/pages/assets/health/PumpMimic';
import PumphouseMimic from '../../src/pages/assets/health/PumphouseMimic';

i18n.load('en', {});
i18n.activate('en');
const client = new QueryClient({
  defaultOptions: { queries: { retry: false } }
});
const params = new URLSearchParams(window.location.search);
const root = document.getElementById('root');
if (root)
  createRoot(root).render(
    <I18nProvider i18n={i18n}>
      <QueryClientProvider client={client}>
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
      </QueryClientProvider>
    </I18nProvider>
  );
