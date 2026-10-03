import { t } from '@lingui/core/macro';
import {
  Anchor,
  Badge,
  Group,
  Loader,
  Stack,
  Table,
  Text
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { StylishText } from '@lib/components/StylishText';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { AssetMachine } from '@lib/types/Assets';
import { useApi } from '../../../contexts/ApiContext';

const WIDGET_LIMIT = 8;

/**
 * Which machines have an alarm standing, worst first.
 *
 * The first question of a shift, answered on the page a shift opens on. It
 * reads the machine list the Machines page reads, filtered to what has an
 * active alarm, so the two can never disagree. A station's count carries its
 * pumps'; each pump with an alarm is listed under its own name as well, and
 * leads to the Health tab where the alarm can be acted on.
 */
export default function MachineAlarmsWidget() {
  const api = useApi();
  const query = useQuery<AssetMachine[]>({
    queryKey: ['dashboard-machine-alarms'],
    refetchInterval: 60000,
    retry: false,
    queryFn: async () => {
      const response = await api.get(apiUrl(ApiEndpoints.asset_machine_list), {
        params: {
          has_alarms: true,
          ordering: '-open_critical_alarms',
          limit: WIDGET_LIMIT
        }
      });
      return response.data?.results ?? response.data ?? [];
    }
  });

  if (query.isLoading) {
    return <Loader size='sm' />;
  }

  const machines = query.data ?? [];

  return (
    <Stack gap='xs'>
      <Group justify='space-between' wrap='nowrap'>
        <StylishText size='md'>{t`Machine alarms`}</StylishText>
        <Anchor component={Link} to='/machines/index/' size='xs'>
          {t`All machines`}
        </Anchor>
      </Group>
      {query.isError ? (
        <Text size='sm' c='dimmed'>
          {t`Machine alarms are unavailable.`}
        </Text>
      ) : !machines.length ? (
        <Text size='sm' c='dimmed'>
          {t`No machine has an active alarm.`}
        </Text>
      ) : (
        <Table>
          <Table.Tbody>
            {machines.map((machine) => {
              const open = machine.open_alarms ?? 0;
              const critical = machine.open_critical_alarms ?? 0;
              return (
                <Table.Tr key={machine.pk} data-machine={machine.pk}>
                  <Table.Td>
                    <Anchor
                      component={Link}
                      to={`/machines/machine/${machine.pk}/health`}
                      size='sm'
                    >
                      {machine.name}
                    </Anchor>
                  </Table.Td>
                  <Table.Td>
                    <Group gap={6} wrap='nowrap' justify='flex-end'>
                      {critical > 0 && (
                        <Badge color='red' variant='filled' size='sm'>
                          {t`${critical} critical`}
                        </Badge>
                      )}
                      {open > critical && (
                        <Badge color='yellow' variant='light' size='sm'>
                          {t`${open - critical} warning`}
                        </Badge>
                      )}
                    </Group>
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}
