import { t } from '@lingui/core/macro';
import { Anchor, Button, Group, Paper, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useApi } from '../../../contexts/ApiContext';
import { useUserState } from '../../../states/UserState';
import { MachineMoveDialog } from './LocationDialogs';
import {
  type LocatedMachine,
  type LocationContext,
  type PageResult,
  locationApi,
  locationPath
} from './locationTypes';

/** Current physical placement beside the preserved legacy machine details. */
export function MachineLocationCard({ machineId }: { machineId: number }) {
  const api = useApi();
  const identity = useUserState((s) => s.authGeneration);
  const [moving, setMoving] = useState(false);
  const context = useQuery<LocationContext>({
    queryKey: ['asset-locations', identity, 'context'],
    queryFn: async ({ signal }) =>
      (await api.get(`${locationApi}context/`, { signal })).data
  });
  const machine = useQuery<PageResult<LocatedMachine>>({
    queryKey: ['asset-locations', identity, 'machine-placement', machineId],
    queryFn: async ({ signal }) =>
      (
        await api.get(`${locationApi}machines/`, {
          signal,
          params: { machine: machineId }
        })
      ).data
  });
  const row = machine.isError ? undefined : machine.data?.results[0];
  return (
    <Paper withBorder p='md'>
      <Stack gap='xs'>
        <Group justify='space-between'>
          <Text fw={600}>{t`Physical location`}</Text>
          {!context.isError && context.data?.can_change && row && (
            <Button
              variant='light'
              size='xs'
              onClick={() => setMoving(true)}
            >{t`Move machine`}</Button>
          )}
        </Group>
        {row ? (
          row.physical_location ? (
            <Anchor
              component={Link}
              to={`/machines/index/sites/?location=${row.physical_location.pk}`}
            >
              {locationPath(row.physical_location)}
            </Anchor>
          ) : (
            <Text c='dimmed'>{t`Unassigned`}</Text>
          )
        ) : (
          <Text c='dimmed'>
            {machine.isPending
              ? t`Loading location…`
              : t`Location is unavailable for your current scope.`}
          </Text>
        )}
        {moving && row && (
          <MachineMoveDialog
            machines={[row]}
            onClose={() => setMoving(false)}
            onSaved={() => setMoving(false)}
          />
        )}
      </Stack>
    </Paper>
  );
}
