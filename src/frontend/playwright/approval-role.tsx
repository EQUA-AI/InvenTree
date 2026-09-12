/** Isolated browser fixture: no credentials, backend or real role mutations. */
import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { MantineProvider } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { createRoot } from 'react-dom/client';
import { RoleTable, type RuleSet } from '../src/components/items/RoleTable';
import { messages } from '../src/locales/en/messages';
import '@mantine/core/styles.css';
import '@mantine/notifications/styles.css';

i18n.load('en', messages);
i18n.activate('en');
const roles = [
  {
    pk: 24,
    group: 12,
    name: 'work_order',
    label: 'Work Orders',
    can_view: false,
    can_add: false,
    can_change: false,
    can_delete: false,
    can_review_approvals: false
  }
] as RuleSet[];

createRoot(document.getElementById('root')!).render(
  <I18nProvider i18n={i18n}>
    <MantineProvider>
      <Notifications />
      <RoleTable roles={roles} editable />
    </MantineProvider>
  </I18nProvider>
);
