import { t } from '@lingui/core/macro';
import {
  Button,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput
} from '@mantine/core';
import { useLocalStorage } from '@mantine/hooks';
import { useState } from 'react';
import { useLocalState } from '../../../states/LocalState';
import { useUserState } from '../../../states/UserState';
import { SharedDisplayViews } from './SharedDisplayViews';
import {
  type DisplayView,
  displayViewKey,
  parseDisplayViews
} from './comparisonViews';

export function DisplayPreferences({
  machineId,
  current,
  onRestore
}: Readonly<{
  machineId: number;
  current: Omit<DisplayView, 'name'>;
  onRestore: (view: DisplayView) => void;
}>) {
  const host = useLocalState((state) => state.hostKey);
  const actor = useUserState((state) => state.user?.pk);
  if (!actor)
    return <Text size='xs'>{t`Sign in to save personal display views.`}</Text>;
  return (
    <ScopedPreferences
      key={JSON.stringify([host, actor, machineId])}
      machineId={machineId}
      scope={displayViewKey(host, actor, machineId)}
      current={current}
      onRestore={onRestore}
    />
  );
}
function ScopedPreferences({
  machineId,
  scope,
  current,
  onRestore
}: Readonly<{
  machineId: number;
  scope: string;
  current: Omit<DisplayView, 'name'>;
  onRestore: (view: DisplayView) => void;
}>) {
  const [views, save] = useLocalStorage<DisplayView[]>({
    key: scope,
    defaultValue: [],
    getInitialValueInEffect: false,
    sync: false,
    deserialize: parseDisplayViews
  });
  const [name, setName] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const persist = (next: DisplayView[]) => {
    try {
      const serialized = JSON.stringify(next);
      // Mantine's storage adapter swallows storage failures. Confirm the write
      // before presenting a saved result or changing the in-memory saved list.
      window.localStorage.setItem(scope, serialized);
      if (window.localStorage.getItem(scope) !== serialized)
        throw new Error('Storage not retained');
      save(next);
      return true;
    } catch {
      setNotice(
        t`Display view could not be saved. Browser storage is unavailable; your current choices remain unsaved.`
      );
      return false;
    }
  };
  return (
    <Stack gap='xs'>
      <Text fw={600} size='sm'>{t`Personal display views`}</Text>
      <Text
        size='xs'
        c='dimmed'
      >{t`Saved only in this browser for this signed-in user, server and machine. Display choices only; no historical evidence or access grant is saved. Changes remain unsaved until Save view.`}</Text>
      <SimpleGrid cols={{ base: 1, sm: 2 }} style={{ alignItems: 'end' }}>
        <TextInput
          label={t`View name`}
          maxLength={80}
          value={name}
          onChange={(event) => setName(event.currentTarget.value)}
        />
        <Button
          variant='light'
          disabled={!name.trim()}
          onClick={() => {
            const view = { ...current, name: name.trim() };
            if (views.some((item) => item.name === view.name)) {
              setNotice(
                t`A view named ${view.name} already exists. Choose another name or explicitly delete that saved view first.`
              );
              return;
            }
            if (views.length >= 20) {
              setNotice(
                t`All 20 view slots are in use. Delete a saved view before saving another. Existing views were kept.`
              );
              return;
            }
            const saved = persist([...views, view]);
            if (!saved) return;
            setSelected(view.name);
            setNotice(t`Display view saved in this browser.`);
          }}
        >{t`Save view`}</Button>
      </SimpleGrid>
      <SimpleGrid cols={{ base: 1, sm: 3 }} style={{ alignItems: 'end' }}>
        <Select
          label={t`Saved view`}
          value={selected}
          onChange={setSelected}
          data={views.map((view) => view.name)}
          placeholder={t`No view selected`}
        />
        <Button
          variant='default'
          disabled={!selected || !views.some((view) => view.name === selected)}
          onClick={() => {
            const view = views.find((item) => item.name === selected);
            if (view) {
              onRestore(view);
              setNotice(
                t`Display view restored. Confirm the current mapping and time range.`
              );
            }
          }}
        >{t`Restore view`}</Button>
        <Button
          variant='subtle'
          disabled={!selected}
          onClick={() => {
            if (persist(views.filter((view) => view.name !== selected))) {
              setSelected(null);
              setNotice(t`Display view deleted locally.`);
            }
          }}
        >{t`Delete view`}</Button>
      </SimpleGrid>
      {notice && (
        <Text component='output' size='xs' style={{ overflowWrap: 'anywhere' }}>
          {notice}
        </Text>
      )}
      <SharedDisplayViews
        machineId={machineId}
        current={current}
        onRestore={onRestore}
      />
    </Stack>
  );
}
