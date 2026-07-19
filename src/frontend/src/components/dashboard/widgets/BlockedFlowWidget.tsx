import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Group,
  Loader,
  Select,
  Stack,
  Text
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { StylishText } from '@lib/components/StylishText';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../../../contexts/ApiContext';
import { useRiskScope } from '../../../hooks/UseRiskScope';
import {
  FlowTiles,
  LocalDateTime,
  parseCommandCenterSummary
} from '../../riskradar/RiskRadarCommon';

/**
 * Dashboard widget showing repair packet / work order flow state counts
 * for the selected scope.
 */
export default function BlockedFlowWidget() {
  const api = useApi();
  const {
    scopes,
    scope,
    authorizationFingerprint,
    setScope,
    unavailable,
    isLoading
  } = useRiskScope();

  const summaryQuery = useQuery({
    queryKey: [
      'command-center-summary',
      scope,
      authorizationFingerprint,
      'widget'
    ],
    enabled: !!scope && !!authorizationFingerprint,
    retry: false,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.command_center_summary), {
          params: { scope: scope }
        })
        .then((response) => parseCommandCenterSummary(response.data, scope))
  });

  if (isLoading || (!!scope && summaryQuery.isLoading)) {
    return <Loader size='sm' />;
  }

  if (unavailable || summaryQuery.isError) {
    return (
      <Stack gap='xs'>
        <StylishText size='md'>{t`Blocked Flow`}</StylishText>
        <Text size='sm' c='dimmed'>
          {t`Flow data is unavailable, or you have no authorized scopes.`}
        </Text>
      </Stack>
    );
  }

  const flow = summaryQuery.data?.flow;
  const degraded = [
    ...(summaryQuery.data?.freshness ?? [])
      .filter((row) => row.degraded || row.source_disabled)
      .map((row) => row.rule),
    ...(summaryQuery.data?.source_freshness ?? [])
      .filter((row) => row.degraded)
      .map((row) => row.source)
  ];

  return (
    <Stack gap='xs'>
      <Group justify='space-between' wrap='nowrap'>
        <StylishText size='md'>{t`Blocked Flow`}</StylishText>
        <Select
          size='xs'
          aria-label='blocked-flow-scope-select'
          data={scopes}
          value={scope}
          onChange={(value) => {
            if (value) {
              setScope(value);
            }
          }}
          allowDeselect={false}
        />
      </Group>
      {summaryQuery.data?.stale && (
        <Alert color='orange' title={t`Stale data`}>
          {t`Data is stale — recommendations withheld`}
        </Alert>
      )}
      {degraded.length > 0 && (
        <Group gap='xs' wrap='wrap'>
          {degraded.map((source) => (
            <Badge key={source} color='orange' variant='light'>
              {source}: {t`degraded`}
            </Badge>
          ))}
        </Group>
      )}
      <FlowTiles title={t`Repair Packets`} counts={flow?.packets} />
      <FlowTiles title={t`Work Orders`} counts={flow?.work_orders} />
      <Text size='xs' c='dimmed'>
        {t`Data as of`} <LocalDateTime value={summaryQuery.data?.as_of} />
      </Text>
    </Stack>
  );
}
