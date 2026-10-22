import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Group,
  Loader,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  TextInput
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../../App';
import { RecordCommandState } from '../../../components/records/RecordCommandState';
import { useRecordedCommand } from '../../../components/records/useRecordedCommand';
import { useLocalState } from '../../../states/LocalState';
import { useUserState } from '../../../states/UserState';
import { displayInstant } from '../displayClock';
import { StatusBadge } from '../health/StatusBadge';
import { MetaText } from '../health/common';
import monitoring from '../monitoring.module.css';
import {
  type TelemetryPresentation,
  recordedTelemetryAsLive
} from '../telemetryPresentation';
import type { DisplayView } from './comparisonViews';
const recordedTime = (value: string) =>
  displayInstant(value) ?? t`Time unavailable`;

type SharedView = {
  id: number;
  name: string;
  config: Omit<DisplayView, 'name'>;
  owner_id: number;
  owner: string | null;
  is_default: boolean;
  version: number;
  recorded_at: string;
  updated_by: string | null;
  updated_at: string;
};
export function SharedDisplayViews({
  machineId,
  current,
  onRestore,
  presentation
}: Readonly<{
  machineId: number;
  current: Omit<DisplayView, 'name'>;
  onRestore: (view: DisplayView) => void;
  presentation?: TelemetryPresentation;
}>) {
  const user = useUserState();
  const host = useLocalState((state) => state.hostKey);
  const [name, setName] = useState('');
  const [read, setRead] = useState(false);
  const endpoint = `${apiUrl(ApiEndpoints.equipment_registry).replace(/registry\/$/, 'recorded/')}${machineId}/performance-views/`;
  const views = useQuery({
    queryKey: [
      'registry',
      'performance-views',
      host,
      user.user?.pk,
      user.authGeneration,
      machineId
    ],
    enabled: read,
    queryFn: async () =>
      (await api.get<{ results: SharedView[] }>(endpoint)).data
  });
  const command = useRecordedCommand<SharedView>(
    endpoint,
    () => {
      setRead(true);
      setName('');
    },
    [['registry', 'performance-views']]
  );
  const locked = command.pending || !!command.retained;
  return (
    <Stack
      gap='sm'
      data-testid='shared-display-views'
      style={{ minWidth: 0, overflowWrap: 'anywhere' }}
    >
      <Text fw={600} size='sm'>{t`Shared display views`}</Text>
      <MetaText size='sm'>
        {recordedTelemetryAsLive(presentation)
          ? t`Visible to everyone who can read this machine. The creator owns each view; engineers curate the default set. Display choices do not save source evidence or grant access.`
          : t`Visible to everyone who can read this machine. The creator owns each view; engineers curate the default set. Display choices do not save historical evidence or grant access.`}
      </MetaText>
      {!read && (
        <Button
          variant='default'
          onClick={() => setRead(true)}
        >{t`Read shared views`}</Button>
      )}
      {read &&
        (views.isPending ? (
          <Loader />
        ) : views.isError ? (
          <Alert color='orange'>
            {t`Shared views unavailable. Personal browser views remain available.`}
            <Button
              onClick={() => views.refetch()}
            >{t`Retry shared views`}</Button>
          </Alert>
        ) : views.data.results.length ? (
          views.data.results.map((view) => (
            <Paper key={view.id} withBorder p='sm'>
              <Stack gap='xs'>
                <Group>
                  <Text fw={600} size='sm'>
                    {view.name}
                  </Text>
                  {view.is_default && (
                    <StatusBadge
                      tone='neutral'
                      variant='outline'
                    >{t`Engineer default`}</StatusBadge>
                  )}
                </Group>
                <MetaText size='sm'>
                  {t`Created by`}: {view.owner ?? t`Not recorded`} ·{' '}
                  {recordedTime(view.recorded_at)}
                </MetaText>
                <MetaText size='sm'>
                  {t`Last recorded by`}: {view.updated_by ?? t`Not recorded`} ·{' '}
                  {recordedTime(view.updated_at)}
                </MetaText>
                <SimpleGrid cols={{ base: 1, sm: 3 }}>
                  <Button
                    variant='default'
                    onClick={() =>
                      onRestore({ ...view.config, name: view.name })
                    }
                  >{t`Restore shared view`}</Button>
                  {user.hasChangeRole(UserRoles.admin) && (
                    <Button
                      variant='light'
                      disabled={locked}
                      onClick={() =>
                        command.submit(
                          'curate',
                          { id: view.id, is_default: !view.is_default },
                          view.version
                        )
                      }
                    >
                      {view.is_default
                        ? t`Remove from defaults`
                        : t`Add to defaults`}
                    </Button>
                  )}
                  {view.owner_id === user.user?.pk && (
                    <Button
                      className={monitoring.link}
                      variant='subtle'
                      disabled={
                        locked ||
                        (view.is_default &&
                          !user.hasChangeRole(UserRoles.admin))
                      }
                      onClick={() =>
                        command.submit('delete', { id: view.id }, view.version)
                      }
                    >{t`Delete shared view`}</Button>
                  )}
                </SimpleGrid>
                {view.owner_id === user.user?.pk &&
                  view.is_default &&
                  !user.hasChangeRole(UserRoles.admin) && (
                    <MetaText size='sm'>{t`An engineer must remove this view from defaults before deletion.`}</MetaText>
                  )}
              </Stack>
            </Paper>
          ))
        ) : (
          <Text size='sm'>{t`No shared views recorded for this machine.`}</Text>
        ))}
      {user.hasChangeRole(UserRoles.work_order) && (
        <>
          <TextInput
            classNames={{ input: monitoring.input }}
            label={t`Shared view name`}
            maxLength={80}
            value={name}
            onChange={(e) => setName(e.currentTarget.value)}
          />
          <Button
            variant='light'
            loading={command.pending}
            disabled={!name.trim() || locked}
            onClick={() =>
              command.submit(
                'create',
                { name: name.trim(), config: current },
                0
              )
            }
          >{t`Share current display view`}</Button>
        </>
      )}
      <RecordCommandState controller={command} />
    </Stack>
  );
}
