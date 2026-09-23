import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { MantineProvider } from '@mantine/core';
import { createRoot } from 'react-dom/client';
import { Boundary } from '../lib/components/Boundary';
import { messages } from '../src/locales/en/messages';
import '@mantine/core/styles.css';

i18n.load('en', messages);
i18n.activate('en');
const loads = Number(sessionStorage.getItem('fixture-loads') ?? 0) + 1;
sessionStorage.setItem('fixture-loads', String(loads));

function BrokenComponent(): never {
  const message = new URLSearchParams(location.search).get('error');
  throw new TypeError(
    message ?? 'Failed to fetch dynamically imported module: /assets/old.js'
  );
}

createRoot(document.getElementById('root')!).render(
  <I18nProvider i18n={i18n}>
    <MantineProvider>
      <div data-testid='loads'>{loads}</div>
      <input aria-label='Unsaved draft' />
      <Boundary label='layout'>
        <BrokenComponent />
      </Boundary>
    </MantineProvider>
  </I18nProvider>
);
