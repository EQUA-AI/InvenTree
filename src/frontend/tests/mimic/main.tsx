import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { MantineProvider } from '@mantine/core';
import '@mantine/core/styles.css';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot } from 'react-dom/client';
import PumphouseMimic from '../../src/pages/assets/health/PumphouseMimic';

i18n.load('en', {});
i18n.activate('en');
const client = new QueryClient({
  defaultOptions: { queries: { retry: false } }
});
const root = document.getElementById('root');
if (root)
  createRoot(root).render(
    <I18nProvider i18n={i18n}>
      <QueryClientProvider client={client}>
        <MantineProvider>
          <PumphouseMimic stationId={17} />
        </MantineProvider>
      </QueryClientProvider>
    </I18nProvider>
  );
