import { t } from '@lingui/core/macro';
import { Anchor, Stack, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { api } from '../../App';
import { MachineComponentTable } from '../../tables/assets/MachineComponentTable';
import { MachinePartTable } from '../../tables/assets/MachinePartTable';

interface PartsMachine {
  pk: number;
  name?: string;
  asset_type?: string;
  parent?: number | null;
}

/**
 * What a machine is made of.
 *
 * Plain equipment has parts someone recorded as fitted. A registered pump or
 * station has, as well, the components the equipment registry identified from
 * its source tags - a different record in a different table, which this tab
 * did not read. Every pump's Installed Parts was empty while the registry held
 * a dozen components for it.
 */
export function InstalledPartsPanel({
  machine
}: Readonly<{ machine: PartsMachine }>) {
  const station = machine.asset_type === 'pumphouse';

  if (!station && machine.asset_type !== 'pump') {
    return <MachinePartTable machineId={machine.pk} />;
  }

  // The registry page selects a station, and optionally one pump under it.
  const registry = station
    ? `/machines/registry/?station=${machine.pk}`
    : `/machines/registry/?station=${machine.parent}&owner=${machine.pk}`;

  return (
    <Stack gap='md'>
      <Text size='sm' c='dimmed'>
        {station
          ? t`The components the equipment registry holds for this station and its pumps.`
          : t`The components the equipment registry holds for this pump.`}{' '}
        {t`A draft was inferred from the station's source tags and has not been confirmed as fitted.`}{' '}
        <Anchor component={Link} to={registry} size='sm'>
          {t`Review in the equipment registry`}
        </Anchor>
      </Text>
      <MachineComponentTable machine={machine} />
      <RecordedParts machineId={machine.pk} />
    </Stack>
  );
}

/**
 * Parts recorded as fitted to a registered machine, when there are any.
 *
 * Nothing records them for a pump today, and an always-empty second table is
 * what made this tab read as broken. But a part someone does record against a
 * pump must not disappear behind its components, so the table is asked for
 * and drawn only once it has a row.
 */
function RecordedParts({ machineId }: Readonly<{ machineId: number }>) {
  const recorded = useQuery({
    queryKey: ['machine-part', 'count', machineId],
    queryFn: async () =>
      (
        await api.get(apiUrl(ApiEndpoints.asset_machine_part_list), {
          params: { machine: machineId, limit: 1 }
        })
      ).data?.count ?? 0
  });

  if (!recorded.data) {
    return null;
  }

  return (
    <Stack gap='xs'>
      <Title order={5}>{t`Recorded as fitted`}</Title>
      <MachinePartTable machineId={machineId} />
    </Stack>
  );
}
