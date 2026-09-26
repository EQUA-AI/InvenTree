import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { MantineProvider } from '@mantine/core';
import '@mantine/core/styles.css';
import '@mantine/charts/styles.css';
import '@mantine/dates/styles.css';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';

import { PerformancePanel } from '../../src/pages/assets/performance/PerformancePanel';

i18n.load('en', {});
i18n.activate('en');

const client = new QueryClient({
  defaultOptions: { queries: { retry: false } }
});

const params = new URLSearchParams(window.location.search);
const machine = {
  pk: Number(params.get('machine') ?? 18),
  name: params.get('name') ?? 'Pump 01',
  asset_type: params.get('type') ?? 'pump'
};

const root = document.getElementById('root');
if (root)
  createRoot(root).render(
    <I18nProvider i18n={i18n}>
      <QueryClientProvider client={client}>
        <MantineProvider>
          <MemoryRouter>
            <PerformancePanel machine={machine} />
          </MemoryRouter>
        </MantineProvider>
      </QueryClientProvider>
    </I18nProvider>
  );
